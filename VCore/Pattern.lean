/-!
# Requests and patterns

A request is a path of segments with the tool first: `Read/src/main.py`,
`WebFetch/pypi.org/simple`, `Bash/git/push/origin`.

A pattern matches whole segments, never characters:

* `a/b`     exactly this path;
* `a/b/**`  this path and everything below it;
* `**/a/b`  every path ending in these segments.

So `Read/secrets/**` does not cover `Read/secrets-public/key`, which a character-level
glob would.

A request is *canonical* when it has at least one segment and no segment is empty, `.`
or `..`. Authorities deny every other request (`Authority.allows_canonical`), so
`Read/./.env` or `Read//.env` is refused instead of slipping past a rule written for
`Read/.env`.
-/

namespace VCore

abbrev Path := List String

/-- A segment that path normalization would leave unchanged. -/
def Segment.canonical (s : String) : Bool := s != "" && s != "." && s != ".."

def Path.canonical (x : Path) : Bool := !x.isEmpty && x.all Segment.canonical

theorem Path.canonical_iff {x : Path} :
    Path.canonical x = true ↔ x ≠ [] ∧ ∀ s ∈ x, Segment.canonical s = true := by
  simp [Path.canonical, List.all_eq_true]

inductive Pat where
  /-- `a/b`: exactly this path. -/
  | exact (p : Path)
  /-- `a/b/**`: this path and everything below it. -/
  | prefixed (p : Path)
  /-- `**/a/b`: every path ending in these segments. -/
  | suffixed (p : Path)
  deriving DecidableEq, Repr, Inhabited

namespace Pat

/-- The literal segments a pattern mentions. -/
def lit : Pat → Path
  | exact p | prefixed p | suffixed p => p

def covers : Pat → Path → Bool
  | exact p, x => p == x
  | prefixed p, x => p.isPrefixOf x
  | suffixed p, x => p.isSuffixOf x

@[simp] theorem covers_exact {p x : Path} : (exact p).covers x = true ↔ p = x := by
  simp [covers]

@[simp] theorem covers_prefixed {p x : Path} : (prefixed p).covers x = true ↔ p <+: x := by
  simp [covers, List.isPrefixOf_iff_prefix]

@[simp] theorem covers_suffixed {p x : Path} : (suffixed p).covers x = true ↔ p <:+ x := by
  simp [covers, List.isSuffixOf_iff_suffix]

end Pat

end VCore
