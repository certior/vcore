import VCore.Policy

/-!
# Compiling policies to a lookup index

The source semantics scans every statement and matches its pattern against the request.
`compile` instead files each statement under its pattern in a table, merging statements
that share one. A request `x` can only be covered by the `2|x| + 3` patterns in `keys x`
(itself exactly, each prefix `x.take i` followed by `**`, each suffix `x.drop i` preceded
by `**`), so a runtime with hash maps decides a request in that many lookups, whatever the
size of the policy.

* `compile_correct`: the index summarizes every request as the source policy does, up to
  the order of tags and combinations, which carries no meaning.
* `compile_allows`, `compile_step`: a compiled authority makes the same decisions and adds
  the same tags as its source.

The Python runtime evaluates this index.
-/

namespace VCore

open Pat

/-- The only patterns that can cover `x`. -/
def keys (x : Path) : List Pat :=
  exact x :: (List.range (x.length + 1)).flatMap fun i => [prefixed (x.take i), suffixed (x.drop i)]

theorem covers_iff_mem_keys {pat : Pat} {x : Path} : pat.covers x = true ↔ pat ∈ keys x := by
  cases pat with
  | exact p =>
    simp only [covers_exact, keys, List.mem_cons, Pat.exact.injEq, List.mem_flatMap,
      List.mem_nil_iff, reduceCtorEq, or_false, exists_false, and_false]
  | prefixed p =>
    simp only [covers_prefixed, keys, List.mem_cons, List.mem_flatMap, List.mem_range,
      List.mem_nil_iff, reduceCtorEq, Pat.prefixed.injEq, or_false, false_or]
    constructor
    · intro h; exact ⟨p.length, Nat.lt_succ_of_le h.length_le, List.prefix_iff_eq_take.1 h⟩
    · rintro ⟨i, -, rfl⟩; exact List.take_prefix _ _
  | suffixed p =>
    simp only [covers_suffixed, keys, List.mem_cons, List.mem_flatMap, List.mem_range,
      List.mem_nil_iff, reduceCtorEq, Pat.suffixed.injEq, or_false, false_or]
    constructor
    · intro h; exact ⟨x.length - p.length, by omega, List.suffix_iff_eq_drop.1 h⟩
    · rintro ⟨i, -, rfl⟩; exact List.drop_suffix _ _

/-- Patterns with the merged summary of the statements filed under them. -/
abbrev Table := List (Pat × Summary)

namespace Table

def get (t : Table) (k : Pat) : Summary :=
  Summary.mergeAll ((t.filter (·.1 == k)).map (·.2))

/-- File `s` under `k`, merging into an existing entry so that keys stay unique. -/
def add : Table → Pat → Summary → Table
  | [], k, s => [(k, s)]
  | (k', s') :: t, k, s => if k' = k then (k', s'.merge s) :: t else (k', s') :: add t k s

theorem get_nil (k : Pat) : get [] k = Summary.empty := rfl

theorem get_cons (k' k : Pat) (s' : Summary) (t : Table) :
    get ((k', s') :: t) k = if k' = k then s'.merge (t.get k) else t.get k := by
  simp only [get, List.filter_cons, beq_iff_eq]; split <;> rfl

end Table

/-- The compiled form of a policy. -/
structure Index where
  table : Table
  deriving Repr, Inhabited

def compile (p : Policy) : Index :=
  ⟨p.foldr (fun st t => t.add st.pat st.eff.summary) []⟩

/-- Runtime evaluation: merge the entries of the `2|x| + 3` candidate keys. -/
def Index.summary (ix : Index) (x : Path) : Summary :=
  Summary.mergeAll ((keys x).map ix.table.get)

/-! ## Observations

A summary is determined, up to `≈`, by four kinds of fact: it permits, it forbids, it has
tag `g`, it has combination `b`. Each holds of a merge exactly when it holds of one side,
so each can be tracked through the table and the key lookup separately. -/

/-- A fact about summaries that holds of a merge iff it holds of one side. -/
structure Obs (O : Summary → Prop) : Prop where
  not_empty : ¬ O Summary.empty
  merge : ∀ s t, O (s.merge t) ↔ O s ∨ O t

namespace Obs

variable {O : Summary → Prop} (h : Obs O)
include h

theorem mergeAll (l : List Summary) : O (Summary.mergeAll l) ↔ ∃ s ∈ l, O s := by
  induction l with
  | nil => simpa [Summary.mergeAll] using h.not_empty
  | cons s l ih => simp only [Summary.mergeAll, List.foldr_cons] at ih ⊢; rw [h.merge, ih]; simp

theorem get_add (t : Table) (k k' : Pat) (s : Summary) :
    O ((t.add k s).get k') ↔ O (t.get k') ∨ (k = k' ∧ O s) := by
  induction t with
  | nil =>
    show O (Table.get [(k, s)] k') ↔ _
    rw [Table.get_cons, Table.get_nil]
    by_cases hk : k = k'
    · simp only [hk, ↓reduceIte, h.merge, true_and]
      exact ⟨fun h' => h'.elim .inr (fun e => absurd e h.not_empty),
        fun h' => h'.elim (fun e => absurd e h.not_empty) .inl⟩
    · simp [hk]
  | cons e t ih =>
    obtain ⟨k₁, s₁⟩ := e
    by_cases h₁ : k₁ = k
    · subst h₁
      have hadd : Table.add ((k₁, s₁) :: t) k₁ s = (k₁, s₁.merge s) :: t := by simp [Table.add]
      rw [hadd, Table.get_cons, Table.get_cons]
      by_cases hk : k₁ = k'
      · simp only [hk, ↓reduceIte, h.merge, true_and]
        constructor
        · rintro ((a | a) | a)
          · exact .inl (.inl a)
          · exact .inr a
          · exact .inl (.inr a)
        · rintro ((a | a) | a)
          · exact .inl (.inl a)
          · exact .inr a
          · exact .inl (.inr a)
      · simp [hk]
    · have hadd : Table.add ((k₁, s₁) :: t) k s = (k₁, s₁) :: Table.add t k s := by simp [Table.add, h₁]
      rw [hadd, Table.get_cons, Table.get_cons]
      by_cases hk : k₁ = k'
      · simp only [hk, ↓reduceIte, h.merge, ih]
        exact or_assoc.symm
      · simp only [hk, ↓reduceIte, ih]

theorem compile_get (p : Policy) (k : Pat) :
    O ((compile p).table.get k) ↔ ∃ st ∈ p, st.pat = k ∧ O st.eff.summary := by
  induction p with
  | nil => simpa [compile, Table.get_nil] using h.not_empty
  | cons st p ih =>
    simp only [compile, List.foldr_cons] at ih ⊢
    rw [h.get_add, ih]
    constructor
    · rintro (⟨st', hst', hk, hO⟩ | ⟨hk, hO⟩)
      · exact ⟨st', List.mem_cons_of_mem _ hst', hk, hO⟩
      · exact ⟨st, List.mem_cons_self .., hk, hO⟩
    · rintro ⟨st', hst', hk, hO⟩
      rcases List.mem_cons.1 hst' with rfl | hst'
      · exact .inr ⟨hk, hO⟩
      · exact .inl ⟨st', hst', hk, hO⟩

theorem summary (p : Policy) (x : Path) :
    O (p.summary x) ↔ ∃ st ∈ p, st.pat.covers x = true ∧ O st.eff.summary := by
  rw [Policy.summary, h.mergeAll]
  simp only [List.mem_map, List.mem_filter]
  constructor
  · rintro ⟨_, ⟨st, ⟨hst, hcov⟩, rfl⟩, hO⟩; exact ⟨st, hst, hcov, hO⟩
  · rintro ⟨st, hst, hcov, hO⟩; exact ⟨_, ⟨st, ⟨hst, hcov⟩, rfl⟩, hO⟩

/-- Looking up the keys of `x` in the index finds exactly the statements covering `x`. -/
theorem index (p : Policy) (x : Path) : O ((compile p).summary x) ↔ O (p.summary x) := by
  rw [Index.summary, h.mergeAll, h.summary]
  simp only [List.mem_map]
  constructor
  · rintro ⟨_, ⟨k, hk, rfl⟩, hO⟩
    obtain ⟨st, hst, hpat, hO'⟩ := (h.compile_get p k).1 hO
    exact ⟨st, hst, covers_iff_mem_keys.2 (hpat ▸ hk), hO'⟩
  · rintro ⟨st, hst, hcov, hO⟩
    exact ⟨_, ⟨st.pat, covers_iff_mem_keys.1 hcov, rfl⟩, (h.compile_get p _).2 ⟨st, hst, rfl, hO⟩⟩

end Obs

theorem obs_permit : Obs (·.permit = true) :=
  ⟨by simp [Summary.empty], fun s t => by simp [Summary.merge]⟩

theorem obs_forbid : Obs (·.forbid = true) :=
  ⟨by simp [Summary.empty], fun s t => by simp [Summary.merge]⟩

theorem obs_label (g : Tag) : Obs (g ∈ ·.labels) :=
  ⟨by simp [Summary.empty], fun s t => by simp [Summary.merge]⟩

theorem obs_block (b : List Tag) : Obs (b ∈ ·.blocks) :=
  ⟨by simp [Summary.empty], fun s t => by simp [Summary.merge]⟩

/-- **Compiler correctness.** The index summarizes every request as the source policy does. -/
theorem compile_correct (p : Policy) (x : Path) : (compile p).summary x ≈ p.summary x :=
  ⟨Bool.eq_iff_iff.2 (obs_permit.index p x), Bool.eq_iff_iff.2 (obs_forbid.index p x),
    fun g => (obs_label g).index p x, fun b => (obs_block b).index p x⟩

/-- The decision of a compiled authority. -/
def Index.allows (ixs : List Index) (ctx : Ctx) (x : Path) : Bool :=
  Path.canonical x && Summary.allowsAll (ixs.map (·.summary x)) ctx

/-- The context after a compiled authority runs `x`. -/
def Index.step (ixs : List Index) (ctx : Ctx) (x : Path) : Ctx :=
  ctx.union (Summary.taintAll (ixs.map (·.summary x)))

/-- A compiled authority decides exactly as its source. -/
theorem compile_allows (a : Authority) (ctx : Ctx) (x : Path) :
    Index.allows (a.map compile) ctx x = a.allows ctx x := by
  simp only [Index.allows, Authority.allows, Authority.summaries, Summary.allowsAll,
    List.map_map, List.isEmpty_map]
  congr 2
  induction a with
  | nil => rfl
  | cons p a ih =>
    simp only [List.map_cons, List.all_cons, Function.comp, (compile_correct p x).allows_eq] at ih ⊢
    rw [ih]

/-- A compiled authority adds exactly the tags of its source. -/
theorem compile_step (a : Authority) (ctx : Ctx) (x : Path) (g : Tag) :
    g ∈ Index.step (a.map compile) ctx x ↔ g ∈ a.step ctx x := by
  simp only [Index.step, Authority.step, Ctx.mem_union, Authority.taint, Authority.summaries,
    Summary.taintAll, List.map_map, List.mem_flatMap, List.mem_map, Function.comp]
  refine or_congr_right ⟨?_, ?_⟩
  · rintro ⟨_, ⟨p, hp, rfl⟩, hg⟩; exact ⟨_, ⟨p, hp, rfl⟩, ((compile_correct p x).2.2.1 g).1 hg⟩
  · rintro ⟨_, ⟨p, hp, rfl⟩, hg⟩; exact ⟨_, ⟨p, hp, rfl⟩, ((compile_correct p x).2.2.1 g).2 hg⟩

end VCore
