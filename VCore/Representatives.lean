import VCore.Refinement

/-!
# Finitely many representative requests

There are infinitely many requests, but a finite set of patterns can only tell finitely
many kinds apart. `reps ps` is a finite list of paths such that every path agrees, on
every pattern in `ps`, with one of them, and a canonical path with a canonical one
(`exists_rep`).

The construction: the exact literals, plus `q ++ [f] ++ u` for every distinct prefix
literal `q` and suffix literal `u` (each possibly empty), where `f` is a segment that no
pattern mentions. A path that is not an exact literal behaves like `q ++ [f] ++ u` for its
longest matching prefix `q` and its longest matching suffix `u`; the fresh segment keeps
patterns from matching across the junction.
-/

namespace VCore

namespace Pat

def exacts (ps : List Pat) : List Path :=
  (ps.filterMap fun | exact p => some p | _ => none).eraseDups

def prefixes (ps : List Pat) : List Path :=
  (ps.filterMap fun | prefixed p => some p | _ => none).eraseDups

def suffixes (ps : List Pat) : List Path :=
  (ps.filterMap fun | suffixed p => some p | _ => none).eraseDups

@[simp] theorem mem_exacts {ps : List Pat} {p : Path} : p ∈ exacts ps ↔ exact p ∈ ps := by
  simp only [exacts, List.mem_eraseDups, List.mem_filterMap]
  constructor
  · rintro ⟨pat, h, e⟩; cases pat <;> simp_all
  · intro h; exact ⟨_, h, rfl⟩

@[simp] theorem mem_prefixes {ps : List Pat} {p : Path} :
    p ∈ prefixes ps ↔ prefixed p ∈ ps := by
  simp only [prefixes, List.mem_eraseDups, List.mem_filterMap]
  constructor
  · rintro ⟨pat, h, e⟩; cases pat <;> simp_all
  · intro h; exact ⟨_, h, rfl⟩

@[simp] theorem mem_suffixes {ps : List Pat} {p : Path} :
    p ∈ suffixes ps ↔ suffixed p ∈ ps := by
  simp only [suffixes, List.mem_eraseDups, List.mem_filterMap]
  constructor
  · rintro ⟨pat, h, e⟩; cases pat <;> simp_all
  · intro h; exact ⟨_, h, rfl⟩

theorem length_le_foldr_max {l : List String} {s : String} (h : s ∈ l) :
    s.length ≤ (l.map String.length).foldr max 0 := by
  induction l with
  | nil => simp at h
  | cons a l ih =>
    simp only [List.map_cons, List.foldr_cons]
    rcases List.mem_cons.1 h with rfl | h
    · omega
    · have := ih h; omega

def underscores (k : Nat) : String := String.ofList (List.replicate k '_')

/-- `_`, `__`, `___`, ...: the shortest run of underscores not in `segs`. -/
def freshFor (segs : List String) : String :=
  let n := (segs.map String.length).foldr max 0
  match ((List.range n).map fun k => underscores (k + 1)).find? (· ∉ segs) with
  | some c => c
  | none => underscores (n + 1)

theorem freshFor_not_mem (segs : List String) : freshFor segs ∉ segs := by
  unfold freshFor
  dsimp only
  split
  · rename_i c hc
    simpa using List.find?_some hc
  · intro hm
    have := length_le_foldr_max hm
    simp [underscores] at this
    omega

theorem underscores_canonical (k : Nat) : Segment.canonical (underscores (k + 1)) = true := by
  have h : ∀ s : String, underscores (k + 1) = s → s.toList = List.replicate (k + 1) '_' := by
    rintro s rfl; simp [underscores]
  simp only [Segment.canonical, Bool.and_eq_true, bne_iff_ne, ne_eq]
  refine ⟨⟨fun e => ?_, fun e => ?_⟩, fun e => ?_⟩ <;>
    have := h _ e <;> cases k <;> simp_all [List.replicate]

theorem freshFor_canonical (segs : List String) : Segment.canonical (freshFor segs) = true := by
  unfold freshFor
  dsimp only
  split
  · rename_i c hc
    obtain ⟨k, -, rfl⟩ := List.mem_map.1 (List.mem_of_find?_eq_some hc)
    exact underscores_canonical k
  · exact underscores_canonical _

/-- A segment that no pattern in `ps` mentions. -/
def fresh (ps : List Pat) : String := freshFor (ps.flatMap lit)

theorem fresh_not_mem (ps : List Pat) {pat : Pat} (hpat : pat ∈ ps) : fresh ps ∉ pat.lit :=
  fun hmem => freshFor_not_mem _ (List.mem_flatMap.2 ⟨pat, hpat, hmem⟩)

def reps (ps : List Pat) : List Path :=
  exacts ps ++ ([] :: prefixes ps).flatMap fun q =>
    ([] :: suffixes ps).map fun u => q ++ fresh ps :: u

theorem exists_longest {P : Path → Prop} {L : List Path} (h : ∃ a ∈ L, P a) :
    ∃ q ∈ L, P q ∧ ∀ p ∈ L, P p → p.length ≤ q.length := by
  induction L with
  | nil => simp at h
  | cons a L ih =>
    by_cases hex : ∃ b ∈ L, P b
    · obtain ⟨q, hq, hPq, hmax⟩ := ih hex
      by_cases ha : P a ∧ q.length < a.length
      · refine ⟨a, List.mem_cons_self .., ha.1, fun p hp hPp => ?_⟩
        rcases List.mem_cons.1 hp with rfl | hp
        · exact Nat.le_refl _
        · have := hmax p hp hPp; omega
      · refine ⟨q, List.mem_cons_of_mem _ hq, hPq, fun p hp hPp => ?_⟩
        rcases List.mem_cons.1 hp with rfl | hp
        · exact Decidable.byContradiction fun hlt => ha ⟨hPp, by omega⟩
        · exact hmax p hp hPp
    · obtain ⟨b, hb, hPb⟩ := h
      rcases List.mem_cons.1 hb with rfl | hb
      · refine ⟨b, List.mem_cons_self .., hPb, fun p hp hPp => ?_⟩
        rcases List.mem_cons.1 hp with rfl | hp
        · exact Nat.le_refl _
        · exact absurd ⟨p, hp, hPp⟩ hex
      · exact absurd ⟨b, hb, hPb⟩ hex

/-- A prefix that avoids segment `f` cannot reach past an occurrence of `f`. -/
theorem prefix_of_prefix_fresh {p q u : Path} {f : String} (hf : f ∉ p)
    (h : p <+: q ++ f :: u) : p <+: q := by
  rcases Nat.lt_or_ge q.length p.length with hl | hl
  · have := h.getElem (i := q.length) hl
    simp only [List.getElem_append_right (Nat.le_refl _), Nat.sub_self,
      List.getElem_cons_zero] at this
    exact absurd (this ▸ List.getElem_mem hl) hf
  · exact List.prefix_of_prefix_length_le h (List.prefix_append q (f :: u)) hl

theorem suffix_of_suffix_fresh {s q u : Path} {f : String} (hf : f ∉ s)
    (h : s <:+ q ++ f :: u) : s <:+ u := by
  rw [← List.reverse_prefix] at h ⊢
  have : (q ++ f :: u).reverse = u.reverse ++ f :: q.reverse := by simp
  rw [this] at h
  exact prefix_of_prefix_fresh (by simpa using hf) h

/-- **Finite representatives.** Every path agrees with some representative on every
pattern in `ps`, and a canonical path has a canonical representative. -/
theorem exists_rep (ps : List Pat) (x : Path) :
    ∃ r ∈ reps ps, (∀ pat ∈ ps, pat.covers x = pat.covers r) ∧
      (Path.canonical x = true → Path.canonical r = true) := by
  by_cases hx : x ∈ exacts ps
  · exact ⟨x, List.mem_append_left _ hx, fun _ _ => rfl, id⟩
  obtain ⟨q, hqL, hqx, hqmax⟩ :=
    exists_longest (P := (· <+: x)) ⟨[], List.mem_cons_self .., List.nil_prefix⟩
  obtain ⟨u, huL, hux, humax⟩ :=
    exists_longest (P := (· <:+ x)) ⟨[], List.mem_cons_self .., List.nil_suffix⟩
  refine ⟨q ++ fresh ps :: u, ?_, fun pat hpat => ?_, fun hcan => ?_⟩
  · exact List.mem_append_right _ (List.mem_flatMap.2 ⟨q, hqL, List.mem_map.2 ⟨u, huL, rfl⟩⟩)
  · have hf := fresh_not_mem ps hpat
    rw [Bool.eq_iff_iff]
    cases pat with
    | exact e =>
      simp only [covers_exact, lit] at hf ⊢
      constructor
      · rintro rfl; exact absurd (mem_exacts.2 hpat) hx
      · rintro rfl; simp at hf
    | prefixed p =>
      simp only [covers_prefixed, lit] at hf ⊢
      constructor
      · intro hp
        have hpq := List.prefix_of_prefix_length_le hp hqx
          (hqmax p (List.mem_cons_of_mem _ (mem_prefixes.2 hpat)) hp)
        exact hpq.trans (List.prefix_append _ _)
      · intro hp; exact (prefix_of_prefix_fresh hf hp).trans hqx
    | suffixed s =>
      simp only [covers_suffixed, lit] at hf ⊢
      constructor
      · intro hs
        have hsu := List.suffix_of_suffix_length_le hs hux
          (humax s (List.mem_cons_of_mem _ (mem_suffixes.2 hpat)) hs)
        exact hsu.trans ((List.suffix_cons _ _).trans (List.suffix_append _ _))
      · intro hs; exact (suffix_of_suffix_fresh hf hs).trans hux
  · -- Every segment of `q` and `u` is a segment of `x`; the fresh one is canonical.
    obtain ⟨-, hseg⟩ := Path.canonical_iff.1 hcan
    refine Path.canonical_iff.2 ⟨by simp, fun s hs => ?_⟩
    simp only [List.mem_append, List.mem_cons] at hs
    rcases hs with hs | rfl | hs
    · exact hseg s (hqx.subset hs)
    · exact freshFor_canonical _
    · exact hseg s (hux.subset hs)

end Pat

/-- Every pattern an authority mentions. -/
def Authority.pats (a : Authority) : List Pat :=
  a.flatMap fun p => p.map (·.pat)

theorem Authority.mem_pats {a : Authority} {p : Policy} {st : Stmt} (hp : p ∈ a)
    (hst : st ∈ p) : st.pat ∈ a.pats :=
  List.mem_flatMap.2 ⟨p, hp, List.mem_map.2 ⟨st, hst, rfl⟩⟩

/-- Representatives for comparing two authorities. -/
def jointReps (old new : Authority) : List Path :=
  Pat.reps (old.pats ++ new.pats)

theorem exists_joint_rep (old new : Authority) (x : Path) :
    ∃ r ∈ jointReps old new, (∀ p ∈ old, p.summary x = p.summary r) ∧
      (∀ p ∈ new, p.summary x = p.summary r) ∧
      (Path.canonical x = true → Path.canonical r = true) := by
  obtain ⟨r, hr, h, hcan⟩ := Pat.exists_rep (old.pats ++ new.pats) x
  exact ⟨r, hr,
    fun p hp => Policy.summary_congr fun st hst =>
      h _ (List.mem_append_left _ (Authority.mem_pats hp hst)),
    fun p hp => Policy.summary_congr fun st hst =>
      h _ (List.mem_append_right _ (Authority.mem_pats hp hst)), hcan⟩

/-- A canonical representative request. -/
def IsRep (old new : Authority) (z : Path) : Prop :=
  z ∈ jointReps old new ∧ Path.canonical z = true

/-- Joint reachability over arbitrary requests is joint reachability over canonical
representatives, in the same number of steps. -/
theorem Reach.to_reps {old new : Authority} {n : Nat} {co cn : Ctx}
    (h : Reach old new (fun _ => True) n co cn) : Reach old new (IsRep old new) n co cn := by
  induction h with
  | start n => exact .start n
  | @step n co cn x _ _ ho hn ih =>
    obtain ⟨r, hr, hold, hnew, hcan⟩ := exists_joint_rep old new x
    have hx := Authority.allows_canonical ho
    have hcr := hcan hx
    rw [Authority.step_congr hold, Authority.step_congr hnew]
    exact .step r ⟨hr, hcr⟩ ih
      (by rwa [← Authority.allows_congr hold (by rw [hx, hcr])])
      (by rwa [← Authority.allows_congr hnew (by rw [hx, hcr])])

end VCore
