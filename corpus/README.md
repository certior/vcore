# Corpus: public workflows that run AI agent actions

Scripts that build the workflow corpus behind the corpus study. Their output goes to
`corpus/data/`, which git ignores. Raw workflow files are never committed or redistributed.

| Target | Action |
|---|---|
| `claude-code-action` (primary) | `anthropics/claude-code-action` |
| `run-gemini-cli` | `google-github-actions/run-gemini-cli` |
| `codex-action` | `openai/codex-action` |

## Pipeline

```
python3 corpus/collect.py    # code search -> data/<target>/hits.jsonl          (slow: rate limited)
python3 corpus/fetch.py      # files at the indexed commit + repository metadata
python3 corpus/clean.py      # filtering funnel, deduplication -> workflows.jsonl, manifest.json
python3 corpus/describe.py   # model-free statistics -> describe.json, describe.md
```

Each script takes an optional list of target names and is safe to interrupt and rerun:
`collect.py` checkpoints its shard queue and the pages it has read, and `fetch.py` skips
anything already downloaded. They need `gh` to be logged in (the token is read with
`gh auth token` and never stored) and PyYAML.

### 1. Collection (`collect.py`)

The search is GitHub's REST code search, one query per target:

```
"<owner>/<action>" path:.github/workflows language:YAML size:LO..HI
```

Code search returns at most 1000 results per query and allows 10 requests per minute. The
query starts with `size:0..400000` (code search does not index files over 384 KB) and any
range whose `total_count` exceeds 1000 is split in half until every range fits. Each range
is then read in pages of 100. Requests are spaced 6.5 s apart; on 403/429 the script
honors `Retry-After` or the rate-limit reset time and otherwise backs off exponentially.
For each result it records the repository, path, git blob id, the commit id from the
result URL, the size range that returned it, and the retrieval time.

### 2. Fetching (`fetch.py`)

Files are downloaded from `raw.githubusercontent.com` at the recorded commit, so each file
is exactly the version that was indexed. Each download is checked against the blob id code
search reported and stored under `data/files/` by its SHA-256. Repository metadata (stars,
fork, archived, private, created and pushed dates, license, primary language, owner type)
comes from the GraphQL API, 50 repositories per query.

### 3. Cleaning (`clean.py`)

Stages, each counted in `data/<target>/manifest.json`, with the reasons for every drop:

1. results reported by code search (sum over ranges);
2. distinct (repository, path) pairs returned;
3. downloaded at the indexed commit;
4. content matches the reported blob id;
5. path is `.github/workflows/<file>.yml|yaml` at the repository root (the only place
   GitHub runs workflows from; nested copies are templates or vendored code);
6. repository metadata available and the repository is public;
7. not a fork;
8. not archived;
9. parses as YAML to a mapping with `jobs`;
10. some job step has `uses: <action>@<ref>` naming the action itself, not a sub-path
    action from the same repository and not a mention in a comment or string;
11. one representative per exact content, keeping the copy count.

YAML is read with YAML 1.2 booleans (only `true`/`false`), as GitHub reads it; PyYAML's
default YAML 1.1 loader would turn the `on:` key into `True`.

Two workflows that differ only in comments, quoting, key order or whitespace share a
`normalized_sha256`, so template copies can be counted even when they were reformatted.

`workflows.jsonl` contains repository names and is for internal analysis only.

### 4. Description (`describe.py`)

Statistics that need no model of the action: triggers, reference pinning, permissions,
inputs set, `claude_args` flags and allowed tools, `allowed_non_write_users` and
`allowed_bots`, `${{ }}` interpolations in prompt inputs, pull-request-head checkouts before
the agent step, and `if:` conditions restricting who runs the agent. Every count is given
per distinct file and weighted by identical copies. The outputs name no repository.

## Snapshot

The collection and cleaning timestamps are recorded in each target's `manifest.json`. The
corpus is a snapshot of each repository's default branch as code search had indexed it at
that time.

## Known biases and limits

* **Code search is not exhaustive.** It indexes default branches only, skips files over
  384 KB, may not index repositories that have been inactive for a long time, and its
  `total_count` is an estimate that can differ between two identical requests. The funnel
  reports both the counts code search claimed and the results actually returned.
* **Ranking and sharding.** Results within a size range are returned by relevance; a range
  that still reports more than 1000 results cannot be read completely and is listed under
  `unsplittable_shards` in the manifest (none is expected, since sizes vary widely).
* **Public repositories only.** Private and internal repositories, where agent workflows may
  be configured differently, are invisible.
* **Point in time.** Files are taken at the commit that was indexed, which may be older
  than the current default branch.
* **Copies.** Many files are verbatim copies of published templates. Statistics are
  reported per distinct file and weighted by copies so that neither view hides the other.
* **Static view.** A workflow file shows configuration, not how often the workflow runs,
  which events actually occur, or repository settings such as default token permissions.

## Neutrality and data release

The study describes how agent workflows are configured; it does not assess or single out
any project.

* No repository, owner or path is named in anything published: statistics are aggregates,
  and released per-workflow records carry derived features under a random identifier.
* Raw workflow files are not redistributed (many repositories have no license); the
  scripts let anyone rebuild the corpus from public data.
* Collection uses only public, documented APIs within their rate limits.

## Reproducing

```
pip install pyyaml
gh auth login
python3 corpus/collect.py claude-code-action
python3 corpus/fetch.py claude-code-action
python3 corpus/clean.py claude-code-action
python3 corpus/describe.py claude-code-action
```

A later run will not return exactly the same files: repositories change, and code search's
index and ranking drift over time. The manifest records when a snapshot was taken.
