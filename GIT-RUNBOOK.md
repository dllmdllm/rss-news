# RSS Git and GitHub runbook

Use this process only for `dllmdllm/rss-news`. Record exact SHAs and run URLs.
Routine publication never authorizes credentials, ownership, permanent trust,
branch protection, permissions, other repositories, or user-data changes.

## Review and code integration

1. Check executor, repository remote, branch and `git status --short`. Leave a
   dirty/unrelated checkout intact. Use a separate branch from fetched official
   main. Read `CLAUDE.md`; regenerate `AGENTS.md` with `tools/sync_agents_md.py`.
2. Inspect the diff and scripts before running tests. Use existing dependencies
   and offline fixtures. Clear provider/message keys for test commands. Never
   run `build.py` as a validation shortcut: it calls configured paid services
   and notifications. Hosted Code checks cover Chromium, worker and code tests.
3. Publish a draft PR with the explicit base/head. For stacked PRs merge the
   parent first, fetch main, integrate while preserving upstream data, retarget
   the child to main and verify its new head. Do not use force push, blanket
   `rebase -X ours`, or branch deletion as a routine repair.
4. Require successful exact-head checks. Data-only upstream commits can coexist;
   unrelated source changes need integration and fresh tests. Merge only under
   user approval, matching the checked head SHA. Never bypass protections.

## Normal publication

- Observe the existing scheduled/dispatcher run; do not create a duplicate run.
  `update.yml` push paths cover `src/**`, `build.py`, and `requirements.txt`.
  Tool/test/workflow-only merges deploy static Pages but wait for the ordinary
  schedule/dispatcher before generated news changes. Do not infer runner status
  from a queue: inspect run/job metadata and concurrency predecessors.
- Expected steps: preflight → retained tracked public site → offline code tests
  → normal fetch/analysis → strict current-schema/evidence validation and data
  tests → explicit generated-file staging → bounded non-force push → recovery
  artifact. A gate failure prevents generated-data publication. After push,
  verify the actual revision's public Pages bytes before acknowledging success;
  retain the per-run verification report. See `OPERATIONS-LOOP.md`.
- Git recovery and publisher commands trust only the resolved current checkout
  for each invocation. No `safe.directory=*`, permanent config or ownership
  change. Archive only tracked public `docs`; exclude secrets/untracked files.
- Push retries first fetch main and compare source/assets with the tested SHA.
  Changed source aborts; allow only generated-data/config changes. Checked mixed
  restacking preserves remote source. Staging/reset/diff errors must fail the
  step; diff status 0 means no changes, 1 means changes, and >1 means error.
- Use explicit UTF-8 when reading/writing repository text and JSON. Do not rely
  on machine locale or modify Windows interpreter/runner settings.

## Evidence of completion

Use read-only `gh pr view`, `gh run view` and `gh run list` to record checked
head, merge SHA, every gate conclusion, pushed data commit and Pages commit.
A successful merge is not proof of data publication. Confirm Pages is built,
then compare deployed assets and generated schema/evidence. Check mobile/desktop
reader, storage-denied/offline behavior, no reload autoplay and current-body
source links. Record whether positive evidence is actually present; do not
invent it. Physical Cantonese audio needs an actual device check.

The existing update job now records the actual pushed/no-change SHA and checks
the public index, core reader assets, metadata and up to five body sidecars.
Its report proves only those selected files; strict prepublication validation
still checks the whole generated snapshot. The check polls at most six times
within 90 seconds and never repeats paid analysis, sends messages, dispatches a
job or rolls back. Failure after push means "publication unverified", not
"nothing was published". Inspect the failed stage/report and let the existing
schedule retry ordinary collection; source code changes require reviewed approval.

## Failure and recovery

Read failed-step logs only and keep credentials/private data out of reports.
Identify first failed stage before proposing action. Do not cancel a running
build or manually dispatch paid analysis as a generic fix. Inspect existing
concurrency/run metadata; settings/ownership corrections need separate approval.

Recovery ZIPs contain revision, validation state and SHA256 checksums and retain
for three days. Verify before restoring into an empty review folder. An invalid
prior snapshot is retained but cannot be restored as known-good. Follow
`ROLLBACK.md` for a reviewed rollback PR; do not force/reset production history.
