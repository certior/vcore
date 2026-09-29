# What is verified

What a `vcore` verdict and a runtime decision rest on, layer by layer. The rule throughout:
nothing is called verified unless a Lean theorem states it.

## Machine-checked

Lean 4, no `sorry` or `admit`, no dependency beyond Lean's standard library. `make axioms`
prints the axioms behind every headline theorem and fails on anything other than Lean's
standard `propext`, `Classical.choice` and `Quot.sound`.

| Claim | Theorem | File |
|---|---|---|
| `vcore check` reports no counterexample exactly when the new authority refines the old: for sessions of every length, over every request | `refines_iff_escalation_eq_none` | [Check.lean](../VCore/Check.lean) |
| A reported counterexample is real: the new authority runs it, the old stops it | `escalation_sound` | Check.lean |
| Whenever refinement fails, it fails within `width + 1` requests | `short_counterexample`, from `Reach.shrink` | Check.lean, [Refinement.lean](../VCore/Refinement.lean) |
| The bound cannot be improved: the lethal-trifecta rule (width 2) needs three requests | `Examples.escalation_needs_three`, `Examples.no_shorter` | [Examples.lean](../VCore/Examples.lean) |
| Finitely many representative paths behave like all paths | `Pat.exists_rep`, `Reach.to_reps` | [Representatives.lean](../VCore/Representatives.lean) |
| The compiled index used at runtime decides and tags exactly as the source policies | `compile_correct`, `compile_allows`, `compile_step` | [Compile.lean](../VCore/Compile.lean) |
| A delegate can never run a sequence its delegator would stop | `delegate_refines` | Refinement.lean |
| The language means what [`language.md`](language.md) says | `allows_nil`, `allows_canonical`, `permitted_of_allows`, `forbid_denies`, `block_denies`, `label_mem_step`, `context_grows`, `summary_perm` | [Policy.lean](../VCore/Policy.lean) |

The proofs guarantee that the checker and the compiler are faithful to the semantics in
`Policy.lean` and `Refinement.lean`. They cannot guarantee that those definitions are the
semantics you want. The contract theorems exist so that a reviewer can check that in a
few lines each, rather than by reading the definitions.

`scripts/mutate_lean.py` seeds bugs into the definitions (a `forbid` that no longer
overrides, a block that fires on one tag, a context that forgets, a compiler that
overwrites, a search one step too shallow, ...) and checks that each one breaks the build.
See [`evaluation.md`](evaluation.md).

## Compiled from verified definitions

`vcore check`, `vcore satisfies`, `vcore eval` and `vcore compile` run the verified
functions (`escalation`, `Authority.allows`, `Authority.step`, `compile`) as compiled by
Lean. Trusted: the Lean compiler and runtime, and the C toolchain.

## Tested, not proved

| Component | Evidence |
|---|---|
| The JSON front end in [`Main.lean`](../Main.lean): parsing, explanations, printing | Every differential test goes through it; CLI tests for messages and exit status |
| The Python parser ([`policy.py`](../python/vcore/policy.py)) | Differential tests against the Lean parser on documents broken in one place: same accept/reject decisions, same statements |
| The Python runtime ([`runtime.py`](../python/vcore/runtime.py)) and index loading | Differential tests against the Lean semantics on random authorities and sessions, including non-canonical requests; seeded runtime bugs, all caught |
| The Z3 checker ([`smt.py`](../python/vcore/smt.py)) | Independent of the Lean checker (own encoding, own search); verdicts, witnesses and witness lengths compared on random policy pairs, searching deeper than the proven bound |
| The tool-call mapping ([`toolcalls.py`](../python/vcore/toolcalls.py)) | Table tests for path resolution, symlinks, URLs, shell commands and malformed input |
| The Claude Code hook ([`examples/claude-code`](../examples/claude-code)) | End-to-end test: a toxic flow is blocked at its third step, sessions are isolated, errors deny |
| Z3, CPython | Trusted |

## Outside the claim

* **Whether a policy is the right one.** `vcore` compares policies and checks properties;
  it does not know what either was meant to allow.
* **The mapping from tool calls to requests.** A policy that forbids `Read/.env` says nothing
  about `Bash/python/-c/...` reading it. The mapping in `toolcalls.py` resolves file paths
  and URLs to one spelling and fails closed on shell syntax, but a shell command's effect is
  not something a request can describe. Treat `Bash` as able to do anything its arguments
  allow, and permit specific commands.
* **Case-insensitive file systems.** On macOS and Windows, `Read/SECRETS/key` and
  `Read/secrets/key` are the same file. The mapping folds case when told to
  (`casefold=True`, `VCORE_CASEFOLD=1`); the core compares segments exactly.
* **Model behaviour and content.** Nothing here inspects prompts, outputs or data. Tags say
  what a session has touched, not what the model did with it.
* **The execution environment.** A compromised host, side channels, and anything that
  happens through requests a policy allows.

## Design choices that fail closed

| Situation | Behaviour | Where |
|---|---|---|
| Empty authority | allows nothing; parsers reject `[]` | `allows_nil`, both parsers |
| Non-canonical request (`.`, `..`, empty segment) | denied | `allows_canonical` |
| Shell command with pipes, substitution, globbing | `Bash/@shell`, which policies do not permit by accident | `toolcalls.py` |
| URL with an encoded `..` or `/`, non-HTTP scheme | a non-canonical request, denied | `toolcalls.py` |
| Hook cannot read the policy or parse its input | the tool call is denied | `vcore_hook.py` |
| Hook allows a call that the user then rejects | the session keeps the call's tags | `vcore_hook.py` |

## Known gaps

* The parsers are not verified. A verified parser, or a proved round trip between parsing
  and printing, would move them into the machine-checked layer.
* The compiled table is not proved to have unique keys; the runtime merges duplicate keys,
  so it does not depend on it.
* The checker's cost grows with (distinct prefix patterns) × (distinct suffix patterns).
  A factorization of representative summaries would remove most of it; it is not done.
