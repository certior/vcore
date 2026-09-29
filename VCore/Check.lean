import VCore.Representatives

/-!
# Deciding refinement

`escalation old new` explores the context pairs that both authorities reach, breadth
first over canonical representative requests and at most `old.width` steps deep, and at
each pair looks for a request that `new` allows and `old` denies.

* `escalationWithin_sound`: a reported sequence is a genuine counterexample.
* `escalationWithin_complete`: if nothing is reported, no counterexample of that length
  exists.
* `refines_iff_escalation_eq_none`: with the default depth, the checker decides
  refinement for sequences of any length over all paths.
* `short_counterexample`: whenever refinement fails, it fails within `old.width + 1`
  requests.

`VCore.Examples` shows the bound is attained.
-/

namespace VCore

/-- What both authorities say about one canonical representative request, computed once. -/
structure Row where
  path : Path
  old : List Summary
  new : List Summary

def rows (old new : Authority) : List Row :=
  ((jointReps old new).filter Path.canonical).map fun z => ⟨z, old.summaries z, new.summaries z⟩

theorem mem_rows {old new : Authority} {r : Row} :
    r ∈ rows old new ↔ IsRep old new r.path ∧ r.old = old.summaries r.path ∧
      r.new = new.summaries r.path := by
  simp only [rows, List.mem_map, List.mem_filter, IsRep]
  constructor
  · rintro ⟨z, ⟨hz, hc⟩, rfl⟩; exact ⟨⟨hz, hc⟩, rfl, rfl⟩
  · rintro ⟨⟨hz, hc⟩, ho, hn⟩; exact ⟨r.path, ⟨hz, hc⟩, by cases r; simp_all⟩

theorem Row.allows_old {old new : Authority} {r : Row} (hr : r ∈ rows old new) (ctx : Ctx) :
    Summary.allowsAll r.old ctx = old.allows ctx r.path := by
  obtain ⟨⟨-, hc⟩, ho, -⟩ := mem_rows.1 hr
  simp [Authority.allows, hc, ho]

theorem Row.allows_new {old new : Authority} {r : Row} (hr : r ∈ rows old new) (ctx : Ctx) :
    Summary.allowsAll r.new ctx = new.allows ctx r.path := by
  obtain ⟨⟨-, hc⟩, -, hn⟩ := mem_rows.1 hr
  simp [Authority.allows, hc, hn]

theorem Row.step_old {old new : Authority} {r : Row} (hr : r ∈ rows old new) (ctx : Ctx) :
    ctx.union (Summary.taintAll r.old) = old.step ctx r.path := by
  obtain ⟨-, ho, -⟩ := mem_rows.1 hr
  simp [Authority.step, Authority.taint, ho]

theorem Row.step_new {old new : Authority} {r : Row} (hr : r ∈ rows old new) (ctx : Ctx) :
    ctx.union (Summary.taintAll r.new) = new.step ctx r.path := by
  obtain ⟨-, -, hn⟩ := mem_rows.1 hr
  simp [Authority.step, Authority.taint, hn]

/-- A joint state with a sequence reaching it. -/
abbrev Entry := (Ctx × Ctx) × List Path

def successors (rs : List Row) (S : List Entry) : List Entry :=
  S.flatMap fun e => rs.filterMap fun r =>
    if Summary.allowsAll r.old e.1.1 && Summary.allowsAll r.new e.1.2 then
      some ((e.1.1.union (Summary.taintAll r.old), e.1.2.union (Summary.taintAll r.new)),
        e.2 ++ [r.path])
    else none

/-- Add an entry unless its joint state is already known. -/
def addNew (S : List Entry) (e : Entry) : List Entry :=
  if S.any (·.1 == e.1) then S else S ++ [e]

/-- The joint states reachable in at most `n` steps, one sequence each. -/
def explore (rs : List Row) : Nat → List Entry
  | 0 => [(([], []), [])]
  | n + 1 => (successors rs (explore rs n)).foldl addNew (explore rs n)

/-- A counterexample of at most `n` requests: a sequence `new` runs and `old` stops. -/
def escalationWithin (n : Nat) (old new : Authority) : Option (List Path) :=
  let rs := rows old new
  if n = 0 then none
  else (explore rs (min (n - 1) old.width)).findSome? fun e =>
    (rs.find? fun r => Summary.allowsAll r.new e.1.2 && !Summary.allowsAll r.old e.1.1).map
      fun r => e.2 ++ [r.path]

/-- A counterexample, if there is one. `old.width + 1` requests always suffice. -/
def escalation (old new : Authority) : Option (List Path) :=
  escalationWithin (old.width + 1) old new

/-! ## Exploration -/

theorem mem_addNew_of_mem {S : List Entry} {e e' : Entry} (h : e' ∈ S) : e' ∈ addNew S e := by
  unfold addNew; split
  · exact h
  · exact List.mem_append_left _ h

theorem mem_of_mem_addNew {S : List Entry} {e e' : Entry} (h : e' ∈ addNew S e) :
    e' ∈ S ∨ e' = e := by
  unfold addNew at h; split at h
  · exact .inl h
  · simpa using h

theorem exists_mem_addNew (S : List Entry) (e : Entry) : ∃ t, (e.1, t) ∈ addNew S e := by
  unfold addNew; split
  · rename_i h
    obtain ⟨e', he', heq⟩ := List.any_eq_true.1 h
    exact ⟨e'.2, by rw [← eq_of_beq heq]; exact he'⟩
  · exact ⟨e.2, by simp⟩

theorem mem_foldl_addNew_of_mem {C S : List Entry} {e : Entry} (h : e ∈ S) :
    e ∈ C.foldl addNew S := by
  induction C generalizing S with
  | nil => exact h
  | cons c C ih => exact ih (mem_addNew_of_mem h)

theorem mem_of_mem_foldl_addNew {C S : List Entry} {e : Entry} (h : e ∈ C.foldl addNew S) :
    e ∈ S ∨ e ∈ C := by
  induction C generalizing S with
  | nil => exact .inl h
  | cons c C ih =>
    rcases ih h with h | h
    · rcases mem_of_mem_addNew h with h | rfl
      · exact .inl h
      · exact .inr (List.mem_cons_self ..)
    · exact .inr (List.mem_cons_of_mem _ h)

theorem exists_mem_foldl_addNew {C S : List Entry} {e : Entry} (h : e ∈ C) :
    ∃ t, (e.1, t) ∈ C.foldl addNew S := by
  induction C generalizing S with
  | nil => simp at h
  | cons c C ih =>
    rcases List.mem_cons.1 h with rfl | h
    · obtain ⟨t, ht⟩ := exists_mem_addNew S e
      exact ⟨t, mem_foldl_addNew_of_mem (C := C) ht⟩
    · exact ih h

section

variable {old new : Authority}

theorem mem_successors {S : List Entry} {e : Entry} (h : e ∈ successors (rows old new) S) :
    ∃ e₀ ∈ S, ∃ r ∈ rows old new, Summary.allowsAll r.old e₀.1.1 = true ∧
      Summary.allowsAll r.new e₀.1.2 = true ∧
      e = ((e₀.1.1.union (Summary.taintAll r.old), e₀.1.2.union (Summary.taintAll r.new)),
        e₀.2 ++ [r.path]) := by
  simp only [successors, List.mem_flatMap, List.mem_filterMap] at h
  obtain ⟨e₀, he₀, r, hr, hsome⟩ := h
  split at hsome
  · rename_i hallow
    simp only [Bool.and_eq_true] at hallow
    exact ⟨e₀, he₀, r, hr, hallow.1, hallow.2, (Option.some.inj hsome).symm⟩
  · simp at hsome

/-- Every explored entry is a sequence both authorities run, ending in its joint state. -/
theorem explore_sound {n : Nat} {e : Entry} (h : e ∈ explore (rows old new) n) :
    old.run [] e.2 = some e.1.1 ∧ new.run [] e.2 = some e.1.2 ∧ e.2.length ≤ n := by
  induction n generalizing e with
  | zero => simp [explore] at h; subst h; exact ⟨rfl, rfl, Nat.le_refl _⟩
  | succ n ih =>
    rcases mem_of_mem_foldl_addNew h with h | h
    · have := ih h; exact ⟨this.1, this.2.1, by omega⟩
    · obtain ⟨e₀, he₀, r, hr, ho, hn, rfl⟩ := mem_successors h
      obtain ⟨h₁, h₂, h₃⟩ := ih he₀
      rw [Row.allows_old hr] at ho
      rw [Row.allows_new hr] at hn
      refine ⟨?_, ?_, by simp; omega⟩
      · simp [Authority.run_append, h₁, Authority.run_singleton, ho, Row.step_old hr]
      · simp [Authority.run_append, h₂, Authority.run_singleton, hn, Row.step_new hr]

theorem explore_mono {n : Nat} {e : Entry} (h : e ∈ explore (rows old new) n) :
    e ∈ explore (rows old new) (n + 1) :=
  mem_foldl_addNew_of_mem h

/-- Every joint state reachable in `n` representative steps is explored. -/
theorem explore_complete {n : Nat} {co cn : Ctx} (h : Reach old new (IsRep old new) n co cn) :
    ∃ t, ((co, cn), t) ∈ explore (rows old new) n := by
  induction h with
  | start n =>
    induction n with
    | zero => exact ⟨[], by simp [explore]⟩
    | succ n ih => obtain ⟨t, ht⟩ := ih; exact ⟨t, explore_mono ht⟩
  | @step n co cn x hx _ ho hn ih =>
    obtain ⟨t, ht⟩ := ih
    have hrow : (⟨x, old.summaries x, new.summaries x⟩ : Row) ∈ rows old new :=
      mem_rows.2 ⟨hx, rfl, rfl⟩
    have hsucc : ((old.step co x, new.step cn x), t ++ [x]) ∈
        successors (rows old new) (explore (rows old new) n) := by
      simp only [successors, List.mem_flatMap, List.mem_filterMap]
      refine ⟨_, ht, _, hrow, ?_⟩
      simp [Row.allows_old hrow, Row.allows_new hrow, ho, hn, Row.step_old hrow,
        Row.step_new hrow]
    exact exists_mem_foldl_addNew hsucc

theorem explore_mono_le {n m : Nat} {e : Entry} (h : e ∈ explore (rows old new) n) (hnm : n ≤ m) :
    e ∈ explore (rows old new) m := by
  induction hnm with
  | refl => exact h
  | step _ ih => exact explore_mono ih

end

/-! ## Correctness -/

/-- **Soundness.** A reported sequence is run by `new`, stopped by `old`, and short. -/
theorem escalationWithin_sound {n : Nat} {old new : Authority} {w : List Path}
    (h : escalationWithin n old new = some w) :
    new.Accepts w ∧ ¬ old.Accepts w ∧ w.length ≤ n ∧ w.length ≤ old.width + 1 := by
  unfold escalationWithin at h
  dsimp only at h
  split at h
  · simp at h
  · obtain ⟨e, he, hw⟩ := List.exists_of_findSome?_eq_some h
    obtain ⟨r, hr, rfl⟩ := Option.map_eq_some_iff.1 hw
    have hrow := List.mem_of_find?_eq_some hr
    have hp := List.find?_some hr
    simp only [Bool.and_eq_true, Bool.not_eq_true', Row.allows_old hrow,
      Row.allows_new hrow] at hp
    obtain ⟨ho, hn, hlen⟩ := explore_sound he
    refine ⟨?_, ?_, by simp; omega, by simp; omega⟩
    · simp [Authority.Accepts, Authority.run_append, hn, Authority.run_singleton, hp.1]
    · simp [Authority.Accepts, Authority.run_append, ho, Authority.run_singleton, hp.2]

/-- If nothing is reported, `old` allows every request that `new` allows after a jointly run
sequence whose length, capped at `old.width`, is below the search depth. -/
theorem escalationWithin_eq_none {n : Nat} {old new : Authority}
    (h : escalationWithin n old new = none) {m : Nat} {co cn : Ctx} {x : Path}
    (hr : Reach old new (fun _ => True) m co cn) (hm : min m old.width < n)
    (hn : new.allows cn x = true) : old.allows co x = true := by
  refine Decidable.byContradiction fun hno => ?_
  simp only [Bool.not_eq_true] at hno
  obtain ⟨k, co', cn', hkw, hkm, hr', hn', ho'⟩ := hr.shrink hn hno
  obtain ⟨z, hz, hold, hnew, hcan⟩ := exists_joint_rep old new x
  have hx := Authority.allows_canonical hn
  have hcz := hcan hx
  have hrow : (⟨z, old.summaries z, new.summaries z⟩ : Row) ∈ rows old new :=
    mem_rows.2 ⟨⟨hz, hcz⟩, rfl, rfl⟩
  obtain ⟨t, ht⟩ := explore_complete hr'.to_reps
  have ht' := explore_mono_le ht (m := min (n - 1) old.width) (by omega)
  unfold escalationWithin at h
  dsimp only at h
  split at h
  · omega
  · have hnone := Option.map_eq_none_iff.1 (List.findSome?_eq_none_iff.1 h _ ht')
    have := List.find?_eq_none.1 hnone _ hrow
    simp only [Row.allows_old hrow, Row.allows_new hrow,
      ← Authority.allows_congr hold (by rw [hx, hcz]),
      ← Authority.allows_congr hnew (by rw [hx, hcz]), hn', ho'] at this
    simp at this

/-- **Bounded completeness.** If nothing is reported, `new` refines `old` on every sequence
of at most `n` requests. -/
theorem escalationWithin_complete {n : Nat} {old new : Authority}
    (h : escalationWithin n old new = none) :
    ∀ w : List Path, w.length ≤ n → new.Accepts w → old.Accepts w := by
  intro w hlen hn
  refine Decidable.byContradiction fun ho => ?_
  obtain ⟨m, co, cn, x, hm, hr, h₁, h₂⟩ := first_failure hn ho
  have := escalationWithin_eq_none h hr (by omega) h₁
  simp [this] at h₂

/-- **Completeness.** If nothing is reported at the default depth, `new` refines `old` on
sequences of every length. -/
theorem escalation_complete {old new : Authority} (h : escalation old new = none) :
    Refines new old := by
  intro w hn
  refine Decidable.byContradiction fun ho => ?_
  obtain ⟨m, co, cn, x, -, hr, h₁, h₂⟩ := first_failure hn ho
  have := escalationWithin_eq_none h hr (by omega) h₁
  simp [this] at h₂

theorem escalation_sound {old new : Authority} {w : List Path}
    (h : escalation old new = some w) :
    new.Accepts w ∧ ¬ old.Accepts w ∧ w.length ≤ old.width + 1 := by
  obtain ⟨h₁, h₂, -, h₃⟩ := escalationWithin_sound h
  exact ⟨h₁, h₂, h₃⟩

/-- **The checker decides refinement.** -/
theorem refines_iff_escalation_eq_none {old new : Authority} :
    Refines new old ↔ escalation old new = none := by
  constructor
  · intro href
    cases hesc : escalation old new with
    | none => rfl
    | some w =>
      obtain ⟨hn, ho, _⟩ := escalation_sound hesc
      exact absurd (href w hn) ho
  · exact escalation_complete

/-- **Short counterexamples.** If `new` does not refine `old`, a sequence of at most
`old.width + 1` requests shows it, however long the sessions that could be run. -/
theorem short_counterexample {old new : Authority} (h : ¬ Refines new old) :
    ∃ w, new.Accepts w ∧ ¬ old.Accepts w ∧ w.length ≤ old.width + 1 := by
  cases hesc : escalation old new with
  | none => exact absurd (escalation_complete hesc) h
  | some w => exact ⟨w, escalation_sound hesc⟩

instance {old new : Authority} : Decidable (Refines new old) :=
  decidable_of_iff _ refines_iff_escalation_eq_none.symm

end VCore
