.PHONY: verify build no-sorry axioms test mutants evaluate clean scenarios oracle fidelity sensitivity corpus-analysis

# Everything a reviewer needs: proofs, axiom audit, tests, mutation study.
verify: build no-sorry axioms test mutants

build:
	lake build

no-sorry:
	@! grep -rnwE "sorry|admit" VCore VCore.lean Main.lean \
		|| (echo "found sorry or admit"; exit 1)

axioms: build
	@lake env lean scripts/Axioms.lean | tee .lake/axioms.txt
	@! grep -oE "\[[^]]*\]" .lake/axioms.txt | tr -d '[] ' | tr ',' '\n' \
		| grep -vxE "propext|Classical.choice|Quot.sound" \
		|| (echo "unexpected axioms"; exit 1)
	@echo "axioms: only propext, Classical.choice, Quot.sound"

test: build
	python3 -m pytest -q

mutants: build
	python3 scripts/mutate_lean.py

# Regenerates docs/evaluation.md, site/results.json and the data in site/index.html.
evaluate: build
	python3 scripts/evaluate.py
	python3 scripts/build_site.py

# --- Agents in GitHub workflows (docs/actions.md) -----------------------------------------

scenarios:
	python3 bench/generate.py > bench/scenarios/generated.jsonl

# Runs the pinned claude-code-action on every scenario (needs bun; see oracle/README.md).
oracle: scenarios
	oracle/fetch.sh
	bun oracle/run.ts bench/scenarios/generated.jsonl --jobs 8 > bench/observations/generated.jsonl

fidelity:
	python3 bench/compare.py bench/scenarios/generated.jsonl bench/observations/generated.jsonl \
		--mismatches bench/results/mismatches.jsonl --json bench/results/fidelity.json

sensitivity: build
	python3 bench/sensitivity.py > bench/results/sensitivity.json

corpus-analysis: build
	python3 corpus/analyze.py --jobs 8 --repairs

clean:
	lake clean
