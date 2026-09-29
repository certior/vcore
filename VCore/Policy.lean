import VCore.Pattern

/-!
# Policies and their meaning on one request

A policy is a list of statements, each a pattern with an effect:

* `permit`        the request may run;
* `forbid`        the request may never run (overrides every `permit`);
* `label t`       running the request adds tag `t` to the session context;
* `block after`   the request may not run once the context holds every tag in `after`.

Tags say what a session has been exposed to (`private`, `untrusted`, ...). A `block`
rule forbids a *combination*: `block WebFetch/** after [private, untrusted]` is the
"lethal trifecta" rule, no egress once the session has both read private data and seen
untrusted content.

What a policy says about a request is the merge of the effects of every statement that
covers it; statement order is irrelevant (`Policy.summary_perm`). An *authority* is a
list of policies that must all allow a request, such as a delegation chain, and it
contributes the tags of all of them.

Contract theorems at the end of this file state these rules against the definitions.
-/

namespace VCore

abbrev Tag := String

/-- The session context: the tags picked up so far. Only membership matters. `Ctx.insert`
keeps the list sorted and free of duplicates, so that equal contexts are equal lists and
the checker can recognise states it has already seen. -/
abbrev Ctx := List Tag

namespace Ctx

def insert (t : Tag) : Ctx → Ctx
  | [] => [t]
  | s :: c => if t = s then s :: c else if t < s then t :: s :: c else s :: insert t c

theorem mem_insert {t u : Tag} {c : Ctx} : u ∈ insert t c ↔ u = t ∨ u ∈ c := by
  induction c with
  | nil => simp [insert]
  | cons s c ih =>
    simp only [insert]
    split
    · rename_i h; subst h; simp only [List.mem_cons]
      constructor
      · intro h; exact .inr h
      · rintro (h | h) <;> simp_all
    · split
      · simp
      · simp only [List.mem_cons, ih]
        constructor
        · rintro (h | h | h) <;> simp_all
        · rintro (h | h | h) <;> simp_all

def union (c : Ctx) (ts : List Tag) : Ctx := ts.foldl (fun c t => insert t c) c

theorem mem_union {c : Ctx} {ts : List Tag} {u : Tag} : u ∈ c.union ts ↔ u ∈ c ∨ u ∈ ts := by
  induction ts generalizing c with
  | nil => simp [union]
  | cons t ts ih =>
    simp only [union, List.foldl_cons] at ih ⊢
    rw [ih, mem_insert, List.mem_cons]
    constructor
    · rintro ((h | h) | h) <;> simp_all
    · rintro (h | h | h) <;> simp_all

end Ctx

inductive Effect where
  | permit
  | forbid
  | label (t : Tag)
  | block (after : List Tag)
  deriving DecidableEq, Repr, Inhabited

structure Stmt where
  pat : Pat
  eff : Effect
  deriving DecidableEq, Repr, Inhabited

abbrev Policy := List Stmt

/-- Everything a policy says about one request. -/
structure Summary where
  permit : Bool
  forbid : Bool
  labels : List Tag
  blocks : List (List Tag)
  deriving DecidableEq, Repr, Inhabited

namespace Summary

def empty : Summary := ⟨false, false, [], []⟩

def merge (s t : Summary) : Summary :=
  ⟨s.permit || t.permit, s.forbid || t.forbid, s.labels ++ t.labels, s.blocks ++ t.blocks⟩

def mergeAll (l : List Summary) : Summary := l.foldr merge empty

/-- Two summaries with the same meaning: the same permit and forbid flags, the same tags
and the same combinations, in any order. -/
def Equiv (s t : Summary) : Prop :=
  s.permit = t.permit ∧ s.forbid = t.forbid ∧
    (∀ g, g ∈ s.labels ↔ g ∈ t.labels) ∧ (∀ b, b ∈ s.blocks ↔ b ∈ t.blocks)

instance : HasEquiv Summary := ⟨Equiv⟩

theorem permit_mergeAll (l : List Summary) : (mergeAll l).permit = l.any (·.permit) := by
  induction l with
  | nil => rfl
  | cons s l ih => simp [mergeAll, merge] at ih ⊢; simp [ih]

theorem forbid_mergeAll (l : List Summary) : (mergeAll l).forbid = l.any (·.forbid) := by
  induction l with
  | nil => rfl
  | cons s l ih => simp [mergeAll, merge] at ih ⊢; simp [ih]

theorem mem_labels_mergeAll {l : List Summary} {g : Tag} :
    g ∈ (mergeAll l).labels ↔ ∃ s ∈ l, g ∈ s.labels := by
  induction l with
  | nil => simp [mergeAll, empty]
  | cons s l ih => simp [mergeAll, merge] at ih ⊢; simp [ih]

theorem mem_blocks_mergeAll {l : List Summary} {b : List Tag} :
    b ∈ (mergeAll l).blocks ↔ ∃ s ∈ l, b ∈ s.blocks := by
  induction l with
  | nil => simp [mergeAll, empty]
  | cons s l ih => simp [mergeAll, merge] at ih ⊢; simp [ih]

/-- Whether some combination of `s` is entirely present in `ctx`. -/
def blocked (s : Summary) (ctx : Ctx) : Bool := s.blocks.any fun b => b.all ctx.contains

theorem blocked_iff {s : Summary} {ctx : Ctx} :
    s.blocked ctx = true ↔ ∃ b ∈ s.blocks, b ⊆ ctx := by
  simp [blocked, List.subset_def]

/-- The decision for a request whose summary is `s`, in context `ctx`. -/
def allows (s : Summary) (ctx : Ctx) : Bool := s.permit && !s.forbid && !s.blocked ctx

theorem allows_iff {s : Summary} {ctx : Ctx} :
    s.allows ctx = true ↔ s.permit = true ∧ s.forbid = false ∧ ∀ b ∈ s.blocks, ¬ b ⊆ ctx := by
  have hb : s.blocked ctx = false ↔ ∀ b ∈ s.blocks, ¬ b ⊆ ctx := by
    rw [Bool.eq_false_iff, ne_eq, blocked_iff]; simp
  simp only [allows, Bool.and_eq_true, Bool.not_eq_true', hb, and_assoc]

/-- Decisions only get stricter as the context grows. -/
theorem allows_antitone {s : Summary} {c₁ c₂ : Ctx} (hc : c₁ ⊆ c₂)
    (h : s.allows c₂ = true) : s.allows c₁ = true := by
  rw [allows_iff] at h ⊢
  exact ⟨h.1, h.2.1, fun b hb hsub => h.2.2 b hb (List.Subset.trans hsub hc)⟩

theorem Equiv.allows_eq {s t : Summary} (h : s ≈ t) (ctx : Ctx) : s.allows ctx = t.allows ctx := by
  obtain ⟨hp, hf, -, hb⟩ := h
  rw [Bool.eq_iff_iff, allows_iff, allows_iff, hp, hf]
  exact and_congr_right fun _ => and_congr_right fun _ =>
    ⟨fun h b hb' => h b ((hb b).2 hb'), fun h b hb' => h b ((hb b).1 hb')⟩

theorem mergeAll_congr {l₁ l₂ : List Summary} (h : ∀ s, s ∈ l₁ ↔ s ∈ l₂) :
    mergeAll l₁ ≈ mergeAll l₂ := by
  refine ⟨?_, ?_, fun g => ?_, fun b => ?_⟩
  · rw [permit_mergeAll, permit_mergeAll, Bool.eq_iff_iff, List.any_eq_true, List.any_eq_true]
    exact ⟨fun ⟨s, hs, hp⟩ => ⟨s, (h s).1 hs, hp⟩, fun ⟨s, hs, hp⟩ => ⟨s, (h s).2 hs, hp⟩⟩
  · rw [forbid_mergeAll, forbid_mergeAll, Bool.eq_iff_iff, List.any_eq_true, List.any_eq_true]
    exact ⟨fun ⟨s, hs, hp⟩ => ⟨s, (h s).1 hs, hp⟩, fun ⟨s, hs, hp⟩ => ⟨s, (h s).2 hs, hp⟩⟩
  · simp only [mem_labels_mergeAll, h]
  · simp only [mem_blocks_mergeAll, h]

/-- The decision of a list of policies, given each one's summary: every policy must
allow, and an empty list allows nothing. -/
def allowsAll (ss : List Summary) (ctx : Ctx) : Bool := !ss.isEmpty && ss.all (·.allows ctx)

/-- The tags a list of policies adds, given each one's summary. -/
def taintAll (ss : List Summary) : List Tag := ss.flatMap (·.labels)

end Summary

def Effect.summary : Effect → Summary
  | .permit => { Summary.empty with permit := true }
  | .forbid => { Summary.empty with forbid := true }
  | .label t => { Summary.empty with labels := [t] }
  | .block b => { Summary.empty with blocks := [b] }

namespace Policy

/-- What policy `p` says about request `x`: the merge of every statement covering it. -/
def summary (p : Policy) (x : Path) : Summary :=
  Summary.mergeAll ((p.filter (·.pat.covers x)).map (·.eff.summary))

theorem summary_congr {p : Policy} {x y : Path}
    (h : ∀ st ∈ p, st.pat.covers x = st.pat.covers y) : p.summary x = p.summary y := by
  unfold summary
  rw [List.filter_congr h]

theorem mem_summaries {p : Policy} {x : Path} {s : Summary} :
    s ∈ (p.filter (·.pat.covers x)).map (·.eff.summary) ↔
      ∃ st ∈ p, st.pat.covers x = true ∧ st.eff.summary = s := by
  simp [List.mem_map, List.mem_filter, and_assoc]

theorem permit_summary {p : Policy} {x : Path} :
    (p.summary x).permit = true ↔ ∃ st ∈ p, st.pat.covers x = true ∧ st.eff = .permit := by
  simp only [summary, Summary.permit_mergeAll, List.any_eq_true, mem_summaries]
  constructor
  · rintro ⟨_, ⟨st, hst, hcov, rfl⟩, h⟩
    exact ⟨st, hst, hcov, by cases h' : st.eff <;> simp_all [Effect.summary, Summary.empty]⟩
  · rintro ⟨st, hst, hcov, heff⟩
    exact ⟨_, ⟨st, hst, hcov, rfl⟩, by simp [heff, Effect.summary]⟩

theorem forbid_summary {p : Policy} {x : Path} :
    (p.summary x).forbid = true ↔ ∃ st ∈ p, st.pat.covers x = true ∧ st.eff = .forbid := by
  simp only [summary, Summary.forbid_mergeAll, List.any_eq_true, mem_summaries]
  constructor
  · rintro ⟨_, ⟨st, hst, hcov, rfl⟩, h⟩
    exact ⟨st, hst, hcov, by cases h' : st.eff <;> simp_all [Effect.summary, Summary.empty]⟩
  · rintro ⟨st, hst, hcov, heff⟩
    exact ⟨_, ⟨st, hst, hcov, rfl⟩, by simp [heff, Effect.summary]⟩

theorem mem_labels_summary {p : Policy} {x : Path} {g : Tag} :
    g ∈ (p.summary x).labels ↔ ∃ st ∈ p, st.pat.covers x = true ∧ st.eff = .label g := by
  simp only [summary, Summary.mem_labels_mergeAll, mem_summaries]
  constructor
  · rintro ⟨_, ⟨st, hst, hcov, rfl⟩, h⟩
    exact ⟨st, hst, hcov, by cases h' : st.eff <;> simp_all [Effect.summary, Summary.empty]⟩
  · rintro ⟨st, hst, hcov, heff⟩
    exact ⟨_, ⟨st, hst, hcov, rfl⟩, by simp [heff, Effect.summary]⟩

theorem mem_blocks_summary {p : Policy} {x : Path} {b : List Tag} :
    b ∈ (p.summary x).blocks ↔ ∃ st ∈ p, st.pat.covers x = true ∧ st.eff = .block b := by
  simp only [summary, Summary.mem_blocks_mergeAll, mem_summaries]
  constructor
  · rintro ⟨_, ⟨st, hst, hcov, rfl⟩, h⟩
    exact ⟨st, hst, hcov, by cases h' : st.eff <;> simp_all [Effect.summary, Summary.empty]⟩
  · rintro ⟨st, hst, hcov, heff⟩
    exact ⟨_, ⟨st, hst, hcov, rfl⟩, by simp [heff, Effect.summary]⟩

/-- **Statement order is irrelevant.** Unlike first-match firewall rules, reordering a
policy never changes what it says about a request. -/
theorem summary_perm {p q : Policy} (h : p.Perm q) (x : Path) : p.summary x ≈ q.summary x :=
  Summary.mergeAll_congr fun _ => ((h.filter _).map _).mem_iff

/-- The largest combination in any `block` statement. -/
def width (p : Policy) : Nat :=
  p.foldr (fun st n => match st.eff with | .block b => max b.length n | _ => n) 0

theorem length_le_width {p : Policy} {st : Stmt} {b : List Tag} (hst : st ∈ p)
    (heff : st.eff = .block b) : b.length ≤ p.width := by
  induction p with
  | nil => simp at hst
  | cons s p ih =>
    simp only [width, List.foldr_cons] at ih ⊢
    rcases List.mem_cons.1 hst with rfl | hst
    · rw [heff]; exact Nat.le_max_left _ _
    · have := ih hst
      split <;> omega

end Policy

/-- A list of policies that must all allow a request: a policy and the policies of every
principal it was delegated through. -/
abbrev Authority := List Policy

namespace Authority

def summaries (a : Authority) (x : Path) : List Summary := a.map (·.summary x)

def allows (a : Authority) (ctx : Ctx) (x : Path) : Bool :=
  Path.canonical x && Summary.allowsAll (a.summaries x) ctx

def taint (a : Authority) (x : Path) : List Tag := Summary.taintAll (a.summaries x)

/-- The session context after running `x` in context `ctx`. -/
def step (a : Authority) (ctx : Ctx) (x : Path) : Ctx := ctx.union (a.taint x)

/-- The largest combination in any policy of the authority. -/
def width (a : Authority) : Nat := a.foldr (fun p n => max p.width n) 0

theorem allows_iff {a : Authority} {ctx : Ctx} {x : Path} :
    a.allows ctx x = true ↔
      Path.canonical x = true ∧ a ≠ [] ∧ ∀ p ∈ a, (p.summary x).allows ctx = true := by
  simp [allows, Summary.allowsAll, summaries, List.all_eq_true]

theorem mem_taint {a : Authority} {x : Path} {g : Tag} :
    g ∈ a.taint x ↔ ∃ p ∈ a, g ∈ (p.summary x).labels := by
  simp only [taint, Summary.taintAll, summaries, List.mem_flatMap, List.mem_map]
  constructor
  · rintro ⟨_, ⟨p, hp, rfl⟩, hg⟩; exact ⟨p, hp, hg⟩
  · rintro ⟨p, hp, hg⟩; exact ⟨_, ⟨p, hp, rfl⟩, hg⟩

theorem mem_step {a : Authority} {ctx : Ctx} {x : Path} {g : Tag} :
    g ∈ a.step ctx x ↔ g ∈ ctx ∨ g ∈ a.taint x := Ctx.mem_union

theorem subset_step (a : Authority) (ctx : Ctx) (x : Path) : ctx ⊆ a.step ctx x := by
  intro g h; exact mem_step.2 (.inl h)

theorem taint_subset_step (a : Authority) (ctx : Ctx) (x : Path) : a.taint x ⊆ a.step ctx x := by
  intro g h; exact mem_step.2 (.inr h)

theorem step_mono {a : Authority} {c₁ c₂ : Ctx} (hc : c₁ ⊆ c₂) (x : Path) :
    a.step c₁ x ⊆ a.step c₂ x := by
  intro g hg
  rcases mem_step.1 hg with h | h
  · exact mem_step.2 (.inl (hc h))
  · exact mem_step.2 (.inr h)

theorem allows_antitone {a : Authority} {c₁ c₂ : Ctx} {x : Path} (hc : c₁ ⊆ c₂)
    (h : a.allows c₂ x = true) : a.allows c₁ x = true := by
  rw [allows_iff] at h ⊢
  exact ⟨h.1, h.2.1, fun p hp => Summary.allows_antitone hc (h.2.2 p hp)⟩

/-- Two canonical requests that every policy summarizes identically are interchangeable. -/
theorem allows_congr {a : Authority} {x y : Path} (h : ∀ p ∈ a, p.summary x = p.summary y)
    (hc : Path.canonical x = Path.canonical y) (ctx : Ctx) : a.allows ctx x = a.allows ctx y := by
  simp only [allows, summaries, hc, List.map_congr_left h]

theorem step_congr {a : Authority} {x y : Path} (h : ∀ p ∈ a, p.summary x = p.summary y)
    (ctx : Ctx) : a.step ctx x = a.step ctx y := by
  simp only [step, taint, summaries, List.map_congr_left h]

theorem length_le_width {a : Authority} {p : Policy} {st : Stmt} {b : List Tag}
    (hp : p ∈ a) (hst : st ∈ p) (heff : st.eff = .block b) : b.length ≤ a.width := by
  induction a with
  | nil => simp at hp
  | cons q a ih =>
    simp only [width, List.foldr_cons] at ih ⊢
    rcases List.mem_cons.1 hp with rfl | hp
    · exact Nat.le_trans (Policy.length_le_width hst heff) (Nat.le_max_left _ _)
    · exact Nat.le_trans (ih hp) (Nat.le_max_right _ _)

/-! ## The contract

What the policy language guarantees, stated against the definitions above. These are
the theorems a reviewer should read to decide whether the semantics is the intended one;
the checker and compiler are proved faithful to it. -/

/-- Fail closed: an authority with no policies allows nothing. -/
theorem allows_nil (ctx : Ctx) (x : Path) : Authority.allows [] ctx x = false := by
  simp [allows, Summary.allowsAll, summaries]

/-- Fail closed: a request that is empty or has an empty, `.` or `..` segment never runs. -/
theorem allows_canonical {a : Authority} {ctx : Ctx} {x : Path} (h : a.allows ctx x = true) :
    Path.canonical x = true :=
  (allows_iff.1 h).1

/-- Default deny: a request runs only if every policy has a `permit` covering it. -/
theorem permitted_of_allows {a : Authority} {ctx : Ctx} {x : Path} (h : a.allows ctx x = true) :
    ∀ p ∈ a, ∃ st ∈ p, st.pat.covers x = true ∧ st.eff = .permit :=
  fun p hp => Policy.permit_summary.1 (Summary.allows_iff.1 ((allows_iff.1 h).2.2 p hp)).1

/-- A `forbid` anywhere overrides every `permit`. -/
theorem forbid_denies {a : Authority} {p : Policy} {st : Stmt} {x : Path} (ctx : Ctx)
    (hp : p ∈ a) (hst : st ∈ p) (hcov : st.pat.covers x = true) (heff : st.eff = .forbid) :
    a.allows ctx x = false := by
  refine Bool.eq_false_iff.2 fun h => ?_
  have := (Summary.allows_iff.1 ((allows_iff.1 h).2.2 p hp)).2.1
  simp [Policy.forbid_summary.2 ⟨st, hst, hcov, heff⟩] at this

/-- A `block` denies a request once the context holds its whole combination. -/
theorem block_denies {a : Authority} {p : Policy} {st : Stmt} {x : Path} {b : List Tag}
    {ctx : Ctx} (hp : p ∈ a) (hst : st ∈ p) (hcov : st.pat.covers x = true)
    (heff : st.eff = .block b) (hb : b ⊆ ctx) : a.allows ctx x = false := by
  refine Bool.eq_false_iff.2 fun h => ?_
  exact (Summary.allows_iff.1 ((allows_iff.1 h).2.2 p hp)).2.2 b
    (Policy.mem_blocks_summary.2 ⟨st, hst, hcov, heff⟩) hb

/-- Running a request adds every tag labelled on it, by any policy. -/
theorem label_mem_step {a : Authority} {p : Policy} {st : Stmt} {x : Path} {g : Tag}
    (ctx : Ctx) (hp : p ∈ a) (hst : st ∈ p) (hcov : st.pat.covers x = true)
    (heff : st.eff = .label g) : g ∈ a.step ctx x :=
  mem_step.2 (.inr (mem_taint.2 ⟨p, hp, Policy.mem_labels_summary.2 ⟨st, hst, hcov, heff⟩⟩))

/-- Tags are never removed: the context only grows. -/
theorem context_grows (a : Authority) (ctx : Ctx) (x : Path) : ctx ⊆ a.step ctx x :=
  subset_step a ctx x

end Authority

end VCore
