import VCore
import Lean.Data.Json

/-!
# `vcore`: command-line front end

Reads policies as JSON, runs the verified functions from `VCore`, and prints the
results. Parsing, explanations and printing are not verified; the differential tests in
`python/tests` compare them with an independent implementation.

Exit status: 0 success (refines / satisfies), 1 counterexample found, 2 bad input or usage.
-/

open Lean VCore

namespace Cli

def version : String := "0.2.0"

/-! ## Surface syntax -/

def parsePath (s : String) : Path := s.splitOn "/"

def renderPath (x : Path) : String := "/".intercalate x

def renderPat : Pat → String
  | .exact p => renderPath p
  | .prefixed [] => "**"
  | .prefixed p => renderPath p ++ "/**"
  | .suffixed p => "**/" ++ renderPath p

def renderTags (ts : List Tag) : String :=
  if ts.isEmpty then "{}" else "{" ++ ", ".intercalate ts ++ "}"

/-- `a/b`, `a/b/**`, `**/a/b` or `**`, with canonical literal segments. -/
def parsePat (s : String) : Except String Pat := do
  let segs := s.splitOn "/"
  let lits (p : Path) : Except String Path := do
    for seg in p do
      if seg == "**" then
        throw s!"pattern '{s}': '**' is only allowed as the first or last segment"
      unless Segment.canonical seg do
        throw s!"pattern '{s}': segments must be non-empty and not '.' or '..'"
    pure p
  if segs == ["**"] then pure (.prefixed [])
  else if segs.head? == some "**" then return .suffixed (← lits segs.tail)
  else if segs.getLast? == some "**" then return .prefixed (← lits segs.dropLast)
  else return .exact (← lits segs)

def parseTag (where_ : String) (j : Json) : Except String Tag := do
  let .str t := j | throw s!"{where_}: expected a tag (a string)"
  if t.isEmpty then throw s!"{where_}: tags must be non-empty"
  pure t

def parseTags (where_ : String) (j : Json) : Except String (List Tag) := do
  let .arr ts := j | throw s!"{where_}: expected a list of tags"
  if ts.isEmpty then throw s!"{where_}: expected at least one tag"
  ts.toList.mapM (parseTag where_)

def fields (where_ : String) (j : Json) (allowed : List String) :
    Except String (List (String × Json)) := do
  let .obj kvs := j | throw s!"{where_}: expected an object"
  let kvs := kvs.toArray.toList.map fun ⟨k, v⟩ => (k, v)
  for (k, _) in kvs do
    unless allowed.contains k do
      throw s!"{where_}: unknown field '{k}' (expected one of {", ".intercalate allowed})"
  pure kvs

/-- `{"permit": [pat], "forbid": [pat], "label": {pat: tag | [tag]},
     "block": [{"on": pat, "after": [tag]}]}` -/
def parsePolicy (where_ : String) (j : Json) : Except String Policy := do
  let kvs ← fields where_ j ["permit", "forbid", "label", "block"]
  let field (k : String) : Option Json := (kvs.find? (·.1 == k)).map (·.2)
  let patterns (k : String) (eff : Effect) : Except String Policy := do
    match field k with
    | none => pure []
    | some (.arr ps) => ps.toList.mapM fun
        | .str p => return { pat := ← parsePat p, eff }
        | _ => throw s!"{where_}.{k}: expected a list of patterns"
    | some _ => throw s!"{where_}.{k}: expected a list of patterns"
  let labels : Except String Policy := do
    match field "label" with
    | none => pure []
    | some (.obj m) =>
      let entries ← m.toArray.toList.mapM fun ⟨p, v⟩ => do
        let pat ← parsePat p
        let ts ← match v with
          | .str _ => pure [← parseTag s!"{where_}.label.\"{p}\"" v]
          | _ => parseTags s!"{where_}.label.\"{p}\"" v
        pure (ts.map fun t => ({ pat, eff := .label t } : Stmt))
      pure entries.flatten
    | some _ => throw s!"{where_}.label: expected an object mapping patterns to tags"
  let blocks : Except String Policy := do
    match field "block" with
    | none => pure []
    | some (.arr rules) => (List.range rules.size).zip rules.toList |>.mapM fun (i, r) => do
        let w := s!"{where_}.block[{i}]"
        let kvs ← fields w r ["on", "after"]
        let some (Json.str on) := (kvs.find? (·.1 == "on")).map (·.2)
          | throw s!"{w}: expected \"on\": a pattern"
        let some after := (kvs.find? (·.1 == "after")).map (·.2)
          | throw s!"{w}: expected \"after\": a list of tags"
        return { pat := ← parsePat on, eff := .block (← parseTags s!"{w}.after" after) }
    | some _ => throw s!"{where_}.block: expected a list of rules"
  return (← patterns "permit" .permit) ++ (← patterns "forbid" .forbid) ++ (← labels) ++ (← blocks)

/-- A policy object, or a non-empty list of policies that must all allow a request. -/
def parseAuthority (j : Json) : Except String Authority :=
  match j with
  | .arr ps =>
    if ps.isEmpty then throw "an authority must have at least one policy"
    else (List.range ps.size).zip ps.toList |>.mapM fun (i, p) => parsePolicy s!"[{i}]" p
  | _ => return [← parsePolicy "policy" j]

/-! ## Explanations -/

/-- Why the policies `ps` (with display names) deny `x` in `ctx`; empty if they allow it. -/
def reasons (ps : List (String × Policy)) (ctx : Ctx) (x : Path) : List String :=
  if !Path.canonical x then ["not a canonical request (empty, '.' or '..' segment)"]
  else if ps.isEmpty then ["no policy"]
  else ps.flatMap fun (name, p) =>
    let s := p.summary x
    let named (msg : String) := if name.isEmpty then msg else s!"{name}: {msg}"
    (if s.permit then [] else [named "no permit"]) ++
    (if s.forbid then [named "forbidden"] else []) ++
    (s.blocks.filter (·.all ctx.contains)).map fun b => named s!"blocked after {", ".intercalate b}"

/-- Display names for the policies of an authority read from one file. -/
def names (a : Authority) : List (String × Policy) :=
  if a.length == 1 then a.map ("", ·) else (List.range a.length).zip a |>.map fun (i, p) => (s!"[{i}]", p)

structure Step where
  allow : Bool
  ctx : Ctx
  why : List String

/-- Decide each request in turn; a denied request leaves the context unchanged. -/
def evalTrace (a : Authority) (named : List (String × Policy)) (t : List Path) : List Step :=
  (t.foldl (fun (ctx, out) x =>
    if a.allows ctx x then (a.step ctx x, out.push ⟨true, a.step ctx x, []⟩)
    else (ctx, out.push ⟨false, ctx, reasons named ctx x⟩)) (([] : Ctx), #[])).2.toList

def stepJson (s : Step) : Json :=
  Json.mkObj [("allow", s.allow), ("context", toJson s.ctx), ("reasons", toJson s.why)]

def pad (s : String) (n : Nat) : String := s.pushn ' ' (n - s.length)

def plural (n : Nat) (word : String) : String := s!"{n} {word}{if n == 1 then "" else "s"}"

def freshNote (old new : Authority) (w : List Path) : String :=
  let fresh := Pat.fresh (old.pats ++ new.pats)
  if w.any (·.contains fresh) then
    s!"\n  '{fresh}' stands for any path segment that neither policy mentions."
  else ""

/-- Print aligned columns. -/
def table (rows : List (List String)) : String :=
  let widths := rows.foldl (fun ws r => (ws.zip r).map fun (w, c) => max w c.length)
    (rows.head?.getD [] |>.map (·.length))
  "\n".intercalate (rows.map fun r =>
    ("  " ++ "  ".intercalate ((r.zip widths).map fun (c, w) => pad c w)).trimAsciiEnd.toString)

def cell (s : Step) : String :=
  if s.allow then s!"allow  {renderTags s.ctx}" else s!"DENY   {"; ".intercalate s.why}"

/-! ## Commands -/

/-- `vcore check`: does `new` refine `old`? -/
def check (oldName newName : String) (old new : Authority) (asJson : Bool) : IO UInt32 := do
  match escalation old new with
  | none =>
    if asJson then IO.println (Json.mkObj [("refines", true)]).compress
    else IO.println s!"{newName} refines {oldName}: it allows no sequence of requests that {oldName} denies."
    pure 0
  | some w =>
    let so := evalTrace old (names old) w
    let sn := evalTrace new (names new) w
    let steps := w.zip (so.zip sn)
    if asJson then
      IO.println (Json.mkObj [("refines", false), ("counterexample", Json.arr (steps.map
        fun (x, o, n) => Json.mkObj [("request", renderPath x), ("old", stepJson o),
          ("new", stepJson n)]).toArray)]).compress
    else
      IO.println s!"{newName} does not refine {oldName}: it allows this sequence of {plural w.length "request"}, which {oldName} denies.\n"
      IO.println (table (["#", "request", oldName, newName] ::
        ((List.range w.length).zip steps).map fun (i, x, o, n) =>
          [toString (i + 1), renderPath x, cell o, cell n]))
      let note := freshNote old new w
      unless note.isEmpty do IO.println note
    pure 1

/-- `vcore satisfies`: does `policy` already enforce `property`? That is, does `policy`
refine the authority that must satisfy both? -/
def satisfies (policyName propertyName : String) (policy property : Authority) (asJson : Bool) :
    IO UInt32 := do
  let both := property ++ policy
  match escalation both policy with
  | none =>
    if asJson then IO.println (Json.mkObj [("satisfies", true)]).compress
    else IO.println s!"{policyName} satisfies {propertyName}: every sequence of requests it allows also satisfies {propertyName}."
    pure 0
  | some w =>
    let steps := evalTrace policy (names policy) w
    let named := property.map (propertyName, ·)
    let ctxBefore := (evalTrace both (named ++ names policy) w).dropLast.getLast?.map (·.ctx) |>.getD []
    let violation := reasons named ctxBefore (w.getLast?.getD [])
    if asJson then
      IO.println (Json.mkObj [("satisfies", false), ("counterexample", Json.arr (w.zip steps |>.map
        fun (x, s) => Json.mkObj [("request", renderPath x), ("policy", stepJson s)]).toArray),
        ("violation", toJson violation)]).compress
    else
      IO.println s!"{policyName} does not satisfy {propertyName}: it allows this sequence of {plural w.length "request"}, which {propertyName} forbids.\n"
      IO.println (table (["#", "request", policyName] ::
        ((List.range w.length).zip (w.zip steps)).map fun (i, x, s) =>
          [toString (i + 1), renderPath x, cell s]))
      IO.println s!"\n  Request {w.length} violates {"; ".intercalate violation}."
      let note := freshNote both policy w
      unless note.isEmpty do IO.println note
    pure 1

def compileJson (p : Policy) : Json :=
  Json.mkObj [("index", Json.arr ((compile p).table.map fun (k, s) =>
    Json.arr #[renderPat k, Json.mkObj [("permit", s.permit), ("forbid", s.forbid),
      ("labels", toJson s.labels), ("blocks", toJson s.blocks)]]).toArray)]

def handle (req : Json) : Except String Json := do
  let auth (k : String) : Except String Authority := do parseAuthority (← req.getObjVal? k)
  match ← req.getObjValAs? String "op" with
  | "parse" =>
    let a ← auth "authority"
    pure (Json.mkObj [("policies", toJson (a.map fun p => p.map fun st =>
      match st.eff with
      | .permit => Json.arr #["permit", renderPat st.pat, Json.null]
      | .forbid => Json.arr #["forbid", renderPat st.pat, Json.null]
      | .label t => Json.arr #["label", renderPat st.pat, toJson t]
      | .block b => Json.arr #["block", renderPat st.pat, toJson b]))])
  | "eval" =>
    let a ← auth "authority"
    let t ← req.getObjValAs? (List String) "trace"
    pure (Json.mkObj [("steps", Json.arr ((evalTrace a (names a) (t.map parsePath)).map stepJson).toArray)])
  | "check" =>
    let old ← auth "old"
    let new ← auth "new"
    let w := match req.getObjValAs? Nat "depth" with
      | .ok n => escalationWithin n old new
      | .error _ => escalation old new
    pure (match w with
      | none => Json.mkObj [("refines", true)]
      | some w => Json.mkObj [("refines", false), ("witness", toJson (w.map renderPath))])
  | "compile" => pure (compileJson (← parsePolicy "policy" (← req.getObjVal? "policy")))
  | "width" => pure (Json.mkObj [("width", (← auth "authority").width)])
  | op => throw s!"unknown op '{op}'"

def usage : String :=
  s!"vcore {version}: verified refinement checking for agent policies

usage:
  vcore check OLD.json NEW.json [--json]
      Does NEW allow any sequence of requests that OLD does not?
  vcore satisfies POLICY.json PROPERTY.json [--json]
      Does POLICY already enforce PROPERTY (does POLICY refine [PROPERTY, POLICY])?
  vcore eval POLICY.json REQUEST...
      Run a session: decide each request in turn.
  vcore compile POLICY.json
      Print the lookup index used by the runtime.

exit status: 0 ok, 1 counterexample, 2 error"

def readAuthority (path : String) : ExceptT String IO Authority := do
  let text ← match ← (IO.FS.readFile path).toBaseIO with
    | .ok text => pure text
    | .error e => throw s!"{path}: {e}"
  let json ← match Json.parse text with
    | .ok j => pure j
    | .error e => throw s!"{path}: invalid JSON: {e}"
  match parseAuthority json with
  | .ok a => pure a
  | .error e => throw s!"{path}: {e}"

def run (args : List String) : ExceptT String IO UInt32 := do
  let asJson := args.contains "--json"
  let args := args.filter (· != "--json")
  match args with
  | ["check", oldPath, newPath] =>
    check oldPath newPath (← readAuthority oldPath) (← readAuthority newPath) asJson
  | ["satisfies", policyPath, propertyPath] =>
    satisfies policyPath propertyPath (← readAuthority policyPath) (← readAuthority propertyPath) asJson
  | "eval" :: path :: reqs@(_ :: _) =>
    let a ← readAuthority path
    let steps := evalTrace a (names a) (reqs.map parsePath)
    IO.println (table ((reqs.zip steps).map fun (x, s) =>
      if s.allow then ["allow", x, s!"context {renderTags s.ctx}"]
      else ["deny", x, "; ".intercalate s.why]))
    pure 0
  | ["compile", path] =>
    match ← readAuthority path with
    | [p] => IO.println (compileJson p).pretty; pure 0
    | _ => throw s!"{path}: compile takes a single policy, not a list"
  | ["batch"] =>
    let stdin ← IO.getStdin
    let stdout ← IO.getStdout
    repeat
      let line ← stdin.getLine
      if line.isEmpty then break
      if line.trimAscii.isEmpty then continue
      let out := match Json.parse line >>= handle with
        | .ok j => j
        | .error e => Json.mkObj [("error", e)]
      stdout.putStrLn out.compress
      stdout.flush
    pure 0
  | ["--version"] => IO.println s!"vcore {version}"; pure 0
  | ["--help"] | ["-h"] | ["help"] => IO.println usage; pure 0
  | _ => throw s!"unrecognized arguments\n\n{usage}"

end Cli

def main (args : List String) : IO UInt32 := do
  match ← (Cli.run args).run with
  | .ok code => pure code
  | .error e =>
    IO.eprintln s!"vcore: error: {e}"
    pure 2
