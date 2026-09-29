import VCore.Policy

/-!
# Sessions, refinement, and short counterexamples

A session is a sequence of requests starting from the empty context. A denied request
stops it, so what an authority permits is the set of sequences it runs to completion.
`Refines new old` says `new` runs no sequence that `old` would stop.

Main results:

* `Reach.shrink`: **few requests set up any denial.** If `old` denies a request that `new`
  allows after some jointly run sequence, the same happens after a subsequence of at most
  `old.width` requests. Contexts only grow and decisions only get stricter as they grow,
  so from the original sequence it is enough to keep, for each tag of the combination
  that blocks the request, one request that adds it.
* `first_failure`: a sequence one authority runs and the other stops has a first request
  where they disagree.
* `delegate_refines`: attaching any policy to an authority never lets more sequences run.

`VCore.Check` turns these into a decision procedure with counterexamples of at
most `old.width + 1` requests.
-/

namespace VCore

namespace Authority

/-- Run the requests `t` from context `ctx`; `none` as soon as one is denied. -/
def run (a : Authority) : Ctx → List Path → Option Ctx
  | ctx, [] => some ctx
  | ctx, x :: t => if a.allows ctx x then a.run (a.step ctx x) t else none

def Accepts (a : Authority) (t : List Path) : Prop := (a.run [] t).isSome

instance (a : Authority) (t : List Path) : Decidable (a.Accepts t) :=
  inferInstanceAs (Decidable ((a.run [] t).isSome = true))

theorem run_append (a : Authority) (ctx : Ctx) (t₁ t₂ : List Path) :
    a.run ctx (t₁ ++ t₂) = (a.run ctx t₁).bind (a.run · t₂) := by
  induction t₁ generalizing ctx with
  | nil => rfl
  | cons x t ih => simp only [List.cons_append, run]; split <;> simp [ih]

theorem run_singleton (a : Authority) (ctx : Ctx) (x : Path) :
    a.run ctx [x] = if a.allows ctx x then some (a.step ctx x) else none := by
  cases h : a.allows ctx x <;> simp [run, h]

end Authority

/-- `new` refines `old`: every sequence `new` runs to completion, `old` runs too. -/
def Refines (new old : Authority) : Prop :=
  ∀ t, new.Accepts t → old.Accepts t

theorem Refines.refl (a : Authority) : Refines a a := fun _ h => h

theorem Refines.trans {a b c : Authority} (h₁ : Refines a b) (h₂ : Refines b c) :
    Refines a c := fun t h => h₂ t (h₁ t h)

/-- Context pairs `(old, new)` that both authorities reach by running the same sequence of
at most `n` requests, each satisfying `R`. -/
inductive Reach (old new : Authority) (R : Path → Prop) : Nat → Ctx → Ctx → Prop
  | start (n : Nat) : Reach old new R n [] []
  | step {n : Nat} {co cn : Ctx} (x : Path) : R x → Reach old new R n co cn →
      old.allows co x = true → new.allows cn x = true →
      Reach old new R (n + 1) (old.step co x) (new.step cn x)

section

variable {old new : Authority} {R : Path → Prop}

theorem Reach.mono {n m : Nat} {co cn : Ctx} (h : Reach old new R n co cn) (hnm : n ≤ m) :
    Reach old new R m co cn := by
  induction h generalizing m with
  | start => exact .start m
  | step x hx _ ho hn ih =>
    obtain ⟨m, rfl⟩ : ∃ k, m = k + 1 := ⟨m - 1, by omega⟩
    exact .step x hx (ih (by omega)) ho hn

theorem Reach.exists_run {n : Nat} {co cn : Ctx} (h : Reach old new R n co cn) :
    ∃ t : List Path, t.length ≤ n ∧ old.run [] t = some co ∧ new.run [] t = some cn := by
  induction h with
  | start => exact ⟨[], Nat.zero_le _, rfl, rfl⟩
  | step x _ _ ho hn ih =>
    obtain ⟨t, hlen, hto, htn⟩ := ih
    exact ⟨t ++ [x], by simp; omega,
      by simp [Authority.run_append, hto, Authority.run_singleton, ho],
      by simp [Authority.run_append, htn, Authority.run_singleton, hn]⟩

/-- **The first disagreement.** If `new` runs a sequence from a jointly reachable pair and
`old` does not, some request is allowed by `new` and denied by `old` after a shorter,
jointly run prefix. -/
theorem Reach.first_failure {n : Nat} {co cn : Ctx} (hr : Reach old new (fun _ => True) n co cn)
    {w : List Path} (hn : (new.run cn w).isSome) (ho : old.run co w = none) :
    ∃ m co' cn' x, m < n + w.length ∧ Reach old new (fun _ => True) m co' cn' ∧
      new.allows cn' x = true ∧ old.allows co' x = false := by
  induction w generalizing n co cn with
  | nil => simp [Authority.run] at ho
  | cons y w ih =>
    simp only [Authority.run] at hn ho
    split at hn
    · rename_i hny
      by_cases hoy : old.allows co y = true
      · simp only [hoy, ↓reduceIte] at ho
        obtain ⟨m, co', cn', x, hm, hr', h₁, h₂⟩ := ih (hr.step y trivial hoy hny) hn ho
        exact ⟨m, co', cn', x, by simp only [List.length_cons]; omega, hr', h₁, h₂⟩
      · exact ⟨n, co, cn, y, by simp only [List.length_cons]; omega, hr, hny, by simpa using hoy⟩
    · simp at hn

theorem first_failure {w : List Path} (hn : new.Accepts w) (ho : ¬ old.Accepts w) :
    ∃ m co cn x, m < w.length ∧ Reach old new (fun _ => True) m co cn ∧
      new.allows cn x = true ∧ old.allows co x = false := by
  obtain ⟨m, co, cn, x, hm, hr, h₁, h₂⟩ :=
    (Reach.start 0).first_failure hn (by simpa [Authority.Accepts] using ho)
  exact ⟨m, co, cn, x, by omega, hr, h₁, h₂⟩

/-- **A few steps set up any combination.** If `(co, cn)` is reachable in `n` steps and
the tags `C` are all in the old context, then some pair `(co', cn')` with `C ⊆ co'`,
`co' ⊆ co` and `cn' ⊆ cn` is reachable in at most `C.length` of those steps. -/
theorem Reach.cover {n : Nat} {co cn : Ctx} (h : Reach old new R n co cn) :
    ∀ C : List Tag, C ⊆ co → ∃ k co' cn', k ≤ C.length ∧ k ≤ n ∧
      Reach old new R k co' cn' ∧ C ⊆ co' ∧ co' ⊆ co ∧ cn' ⊆ cn := by
  induction h with
  | start n =>
    intro C hC
    exact ⟨0, [], [], Nat.zero_le _, Nat.zero_le _, .start 0, hC, List.Subset.refl _,
      List.Subset.refl _⟩
  | @step n co cn x hx _ ho hn ih =>
    intro C hC
    -- The tags of `C` that `x` does not add were already in `co`.
    let C₁ := C.filter fun g => !(old.taint x).contains g
    have hC₁ : C₁ ⊆ co := by
      intro g hg
      obtain ⟨hgC, hnot⟩ := List.mem_filter.1 hg
      rcases Authority.mem_step.1 (hC hgC) with h | h
      · exact h
      · simp [h] at hnot
    obtain ⟨k, co₁, cn₁, hk, hkn, hr₁, hsub, hco, hcn⟩ := ih C₁ hC₁
    by_cases hx : ∃ g ∈ C, g ∈ old.taint x
    · -- `x` adds a tag of `C`: keep it.
      have hlt : C₁.length < C.length := by
        refine List.length_filter_lt_length_iff_exists.2 ?_
        obtain ⟨g, hg, hgx⟩ := hx
        exact ⟨g, hg, by simp [hgx]⟩
      refine ⟨k + 1, old.step co₁ x, new.step cn₁ x, by omega, by omega,
        .step x ‹R x› hr₁ (Authority.allows_antitone hco ho) (Authority.allows_antitone hcn hn),
        ?_, Authority.step_mono hco x, Authority.step_mono hcn x⟩
      intro g hg
      by_cases hgx : g ∈ old.taint x
      · exact Authority.taint_subset_step _ _ _ hgx
      · exact Authority.subset_step _ _ _ (hsub (List.mem_filter.2 ⟨hg, by simp [hgx]⟩))
    · -- `x` adds nothing to `C`: drop it.
      have hCC₁ : C ⊆ C₁ := by
        intro g hg
        exact List.mem_filter.2 ⟨hg, by simpa using fun h => hx ⟨g, hg, h⟩⟩
      refine ⟨k, co₁, cn₁, Nat.le_trans hk (List.length_filter_le _ _), by omega, hr₁,
        List.Subset.trans hCC₁ hsub, List.Subset.trans hco (Authority.subset_step _ _ _),
        List.Subset.trans hcn (Authority.subset_step _ _ _)⟩

/-- **Short prefixes suffice.** If `new` allows `x` and `old` denies it after a jointly run
sequence, the same happens after at most `old.width` of those requests. -/
theorem Reach.shrink {n : Nat} {co cn : Ctx} {x : Path} (hr : Reach old new R n co cn)
    (hn : new.allows cn x = true) (ho : old.allows co x = false) :
    ∃ k co' cn', k ≤ old.width ∧ k ≤ n ∧ Reach old new R k co' cn' ∧
      new.allows cn' x = true ∧ old.allows co' x = false := by
  have hn₀ : new.allows [] x = true := Authority.allows_antitone (List.nil_subset _) hn
  by_cases ho₀ : old.allows [] x = true
  · -- `old` allows `x` from the start, so a combination blocks it in `co`.
    obtain ⟨hcan, hne, hall⟩ := Authority.allows_iff.1 ho₀
    have : ∃ p ∈ old, (p.summary x).allows co = false := by
      refine Decidable.byContradiction fun hno => ?_
      have : old.allows co x = true :=
        Authority.allows_iff.2 ⟨hcan, hne, fun p hp => by simpa using fun h => hno ⟨p, hp, h⟩⟩
      simp [this] at ho
    obtain ⟨p, hp, hpco⟩ := this
    obtain ⟨hperm, hforb, -⟩ := Summary.allows_iff.1 (hall p hp)
    obtain ⟨b, hb, hbco⟩ : ∃ b ∈ (p.summary x).blocks, b ⊆ co := by
      refine Decidable.byContradiction fun hno => ?_
      have : (p.summary x).allows co = true :=
        Summary.allows_iff.2 ⟨hperm, hforb, fun b hb hsub => hno ⟨b, hb, hsub⟩⟩
      simp [this] at hpco
    obtain ⟨st, hst, hcov, heff⟩ := Policy.mem_blocks_summary.1 hb
    obtain ⟨k, co', cn', hk, hkn, hr', hbco', -, hcn'⟩ := hr.cover b hbco
    exact ⟨k, co', cn', Nat.le_trans hk (Authority.length_le_width hp hst heff), hkn, hr',
      Authority.allows_antitone hcn' hn, Authority.block_denies hp hst hcov heff hbco'⟩
  · -- `old` denies `x` whatever the context.
    exact ⟨0, [], [], Nat.zero_le _, Nat.zero_le _, .start 0, hn₀, by simpa using ho₀⟩

end

/-- **Delegation never escalates.** Whatever policy a principal attaches when delegating,
the delegate runs no sequence its delegator would stop. -/
theorem delegate_refines (child : Policy) (a : Authority) (ha : a ≠ []) :
    Refines (child :: a) a := by
  intro t
  suffices ∀ cd ca, ca ⊆ cd → (Authority.run (child :: a) cd t).isSome → (a.run ca t).isSome from
    this [] [] (List.Subset.refl _)
  induction t with
  | nil => intro _ _ _ _; rfl
  | cons x t ih =>
    intro cd ca hsub hrun
    simp only [Authority.run] at hrun ⊢
    split at hrun
    · rename_i hd
      obtain ⟨hcan, -, hall⟩ := Authority.allows_iff.1 hd
      have ha' : a.allows cd x = true :=
        Authority.allows_iff.2 ⟨hcan, ha, fun p hp => hall p (List.mem_cons_of_mem _ hp)⟩
      simp only [Authority.allows_antitone hsub ha', ↓reduceIte]
      refine ih _ _ ?_ hrun
      intro g hg
      rcases Authority.mem_step.1 hg with h | h
      · exact Authority.subset_step _ _ _ (hsub h)
      · obtain ⟨p, hp, hgp⟩ := Authority.mem_taint.1 h
        exact Authority.taint_subset_step _ _ _
          (Authority.mem_taint.2 ⟨p, List.mem_cons_of_mem _ hp, hgp⟩)
    · simp at hrun

end VCore
