import VCore

/-! The axioms behind every headline theorem. `make axioms` fails on anything other than
Lean's standard `propext`, `Classical.choice` and `Quot.sound` (in particular `sorryAx`). -/

open VCore

-- Deciding refinement
#print axioms refines_iff_escalation_eq_none
#print axioms escalation_sound
#print axioms escalation_complete
#print axioms escalationWithin_complete
#print axioms short_counterexample
-- The two reductions behind it
#print axioms Reach.shrink
#print axioms Pat.exists_rep
-- The bound is attained
#print axioms Examples.escalation_needs_three
#print axioms Examples.no_shorter
-- Compilation
#print axioms compile_correct
#print axioms compile_allows
#print axioms compile_step
-- Delegation and the policy contract
#print axioms delegate_refines
#print axioms Authority.allows_nil
#print axioms Authority.allows_canonical
#print axioms Authority.permitted_of_allows
#print axioms Authority.forbid_denies
#print axioms Authority.block_denies
#print axioms Authority.label_mem_step
#print axioms Authority.context_grows
#print axioms Policy.summary_perm
