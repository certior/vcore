import VCore.Check

/-!
# The bound is attained

`short_counterexample` bounds counterexamples by `old.width + 1` requests. This example
shows the bound cannot be improved: the lethal-trifecta rule has width 2, and removing it
opens an escalation that needs three requests. No sequence of two will do, because no
single request carries both `private` and `untrusted`.
-/

namespace VCore.Examples

/-- Read private data, fetch untrusted content, send: blocked after the first two. -/
def trifecta : Authority :=
  [[⟨.prefixed [], .permit⟩,
    ⟨.prefixed ["Read"], .label "private"⟩,
    ⟨.prefixed ["Fetch"], .label "untrusted"⟩,
    ⟨.prefixed ["Send"], .block ["private", "untrusted"]⟩]]

/-- The same policy without the combination rule. -/
def unguarded : Authority :=
  [[⟨.prefixed [], .permit⟩,
    ⟨.prefixed ["Read"], .label "private"⟩,
    ⟨.prefixed ["Fetch"], .label "untrusted"⟩]]

theorem trifecta_width : trifecta.width = 2 := by decide

/-- The checker finds a three-request escalation... -/
theorem escalation_needs_three :
    escalation trifecta unguarded = some [["Read", "_"], ["Fetch", "_"], ["Send", "_"]] := by
  decide

/-- ...and there is none with two requests or fewer. -/
theorem no_shorter : ∀ w : List Path, w.length ≤ 2 → unguarded.Accepts w → trifecta.Accepts w :=
  escalationWithin_complete (by decide)

theorem not_refines : ¬ Refines unguarded trifecta := by decide

end VCore.Examples
