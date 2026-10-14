# Source, article media and ordering QA

This is the reusable checking runbook requested on 2026-10-13. It authorizes no
new release, provider, credential, recipient, schedule or runner change. Current
evidence and the complete source matrix are in
[the dated audit](SOURCE-MEDIA-AUDIT-2026-10-13.md).

## What must be distinguished

- A configured feed is not a distinct publisher. Read `RSS_FEEDS` at the tested
  revision; never hard-code the source count. Feed count, unique collected count,
  retained count, visible deduplicated count and playable count are different.
- HTTP 200 is not feed/body/image success. Count parsed entries, reject HTML
  challenges, inspect image MIME and successful decode. A placeholder is not a
  successfully loaded news picture.
- Feed success is not full-body success. Public API body, full RSS body, RSS
  excerpt, browser-visible article, retained verified body and minimal fallback
  need separate provenance. `rss-blocked` alone cannot tell whether the retained
  WordPress API body is complete.
- One hero plus zero body images can be intentional featured-image deduplication.
  Zero hero plus zero body images needs original-source inspection. Do not invent
  images or fetch replacements from another provider.
- Captions, quotations and tables are editorial content. A parser preserving
  text/image blocks while silently dropping these is not a complete extraction.
- Keep article body order separate from card order. The unfiltered desktop list
  groups categories; the player uses the global selected sort. Source-filtered
  list/lead and player must agree after missing-sentence entries are excluded.
  Never infer a global-order defect by concatenating category sections.
- Mac reads prove only Mac reads. Existing Windows run metadata and its actual
  published source-health snapshot establish production observations. A prior
  CAPTCHA result does not invalidate a later verified public-API recovery.

## Reproducible audit sequence

1. Read `AGENTS.md`, `CLAUDE.md`, `GIT-RUNBOOK.md`, `OPERATIONS-LOOP.md` and any
   actually present related local skills. Check executor, remote, worktrees,
   uncommitted files and running publisher. Fetch official main read-only and
   pin SHA, snapshot timestamp and configuration. Use an isolated audit branch.
2. Clear AI/message environment variables. Do not import/run the `build.py`
   entry point or `fetch_all()` for an audit: the latter persists HTTP validators
   and feed readers can translate titles when a provider key is configured.
   A diagnostic may call the existing per-feed reader with an empty in-memory
   cache, an empty provider key and a fail-closed paid-call stub. Never write
   production feed, analysis, translation or delivery caches.
3. Check **every active article** offline: one ID and canonical URL, aware UTC
   dates, no unreasonable future date, exact body hash, existing nonempty sidecar,
   metadata/index identity, image URLs, repeated occurrences, text length,
   missing sentences, retention/fallback provenance. Run
   `python tools/validate_publication.py docs --require-current` and applicable
   existing tests with provider and notification variables cleared.
4. Read each configured public source once using existing guarded readers.
   Record timestamp, collection URL/method, status, parsed count and canonical
   IDs. Bound concurrency at four, retain existing request/per-feed timeouts and
   cap each diagnostic task. Stop on denial, unsafe destination or TLS failure;
   no alternate cookies, clients, proxy, token or access-control bypass.
5. Choose a deterministic newest published article per source; also rotate a
   multi-image/partial-body sample where useful. Record exact article URL, ID,
   dates, body hash, original response hash and coverage denominator. Compare
   original editorial blocks and their interleaved text/image/caption order.
   For hydrated sources inspect the public embedded article JSON, not navigation,
   repeated recommendations or a flattened HTML text search. Compare quote/table
   block types explicitly. For translated sources verify structure without paid
   retranslation; semantic fidelity remains a separately labelled manual sample.
6. A changed current original is not proof that the old extraction reordered
   content. Compare hashes and source modification times; retain the original
   capture/checkpoint needed to distinguish a source revision from a parser bug.
7. Compare live metadata and deterministic selected sidecar bytes with the pinned
   commit. In isolated Chromium check title, body sequence after sanitization,
   intentional hero deduplication, thumbnail-to-ID association, missing-sentence
   placeholders, per-source three-mode sort and mobile overflow. Scroll sampled
   images into view and require `naturalWidth > 0`; mark untested images unknown.
   Record sample counts, load failures and console errors. Service workers blocked
   in test Chromium do not prove installed-client/Safari behavior.
8. Classify each dimension PASS / FAIL / UNVERIFIED / SOURCE-LIMITATION. Link
   evidence. A green integrity gate does not prove complete original content.
   Never report one article/three images as every article/image passing.

## Bounded loop implementation (approved 2026-10-13; rollout gated)

Reuse the existing publisher concurrency lock and 20-minute cycle. No new timer,
workflow dispatch, paid retry or self-generated repair code. `src/media_qa.py`
and its CPU worker implement the stage below. Until exact-head CI, deployment
and Windows observation complete, code presence is not production success:

1. Offline inventory covers all current articles after collection/extraction and
   before enrichment/publication. Reuse source/body/analysis hashes. Detect unsafe
   thumbnails, missing sidecars, hash mismatch, output-schema drift and unsupported
   editorial blocks; these must not become falsely healthy source observations.
2. Rotate at most six source samples per ordinary cycle, one original plus up to
   three selected images per source. With 37 configured sources this is at least
   seven cycles; derive the cursor from config and carry it across interruptions.
   Use six public connections, eight-second image requests, existing 15-second
   body requests, a 60-second whole QA budget, response-size and redirect/DNS/TLS
   guards. Budget expiry records UNVERIFIED and resumes next existing cycle.
   The budget starts before inventory: 58 seconds work plus 2 seconds to kill/reap
   CPU subprocesses. No parser/decoder thread survives cancellation. Pillow
   12.3.0 is a pinned ordinary app dependency installed by the existing requirements
   step; no runner settings/browser-install change. Verify and decode one frame,
   at most 20 million pixels. [Decoder reference](https://pillow.readthedocs.io/).
   Static browser regressions stay in existing Code checks.
3. Checkpoint fields in `docs/data/media_qa.json`: config hash, revision, source/article ID, body hash,
   original hash/source modification time, checked time, image URL/hash/decode
   result, block-type/order signature, failure fingerprint, cursor, attempt count,
   next-eligible time and last acknowledged transition. Atomic writes, schema
   validation, bounded records/retention and the existing concurrency lock avoid
   partial state or simultaneous writers. Public reports contain metadata/hashes,
   never credentials, cookies or private content.
4. Temporary network/image failures get no more than one bounded retry within the
   QA budget, then backoff through existing cycles: 20/40/80 minutes, capped at
   six hours. Denial/TLS/unsafe destinations receive no immediate retry or alternate
   access method. Quiet feed and validated 304 differ from failure. Retained
   last-good content remains honestly labelled; it cannot count as fresh recovery.
5. Existing source-health handles fresh-feed transitions. Media health
   keys on source + failure kind + article/body/image hash. Record new problem and
   recovery once after confirmed observation; repeat only on a changed fingerprint
   or existing reminder policy. Integrate existing recipient/acknowledgement
   handling within the approved scope, with no new recipient or channel. Failed
   delivery stays pending. An unverified cycle preserves pending failures but
   cancels pending recovery. Delivery has a separate bounded 30-second allowance.
6. Hard integrity/unsafe-field failures stop before publication. Source limitations
   or uncertain media samples retain safe last-good content and honest warnings;
   they do not demand an AI rerun. Image repetition alone needs source comparison
   before deletion. Refetch a changed original through the approved normal reader,
   preserve current-body evidence guards, and invalidate enrichment only if the
   actual body changed under the existing authorized build policy.
7. After any data push, preserve existing exact-pushed-SHA Pages verification.
   Check selected live metadata/body/image association. A transport failure after
   push is publication-unverified; resume public verification from its checkpoint
   in the next existing cycle, not paid analysis. The existing workflow verifies
   its checked-out exact SHA before paid collection and stops on read failure.
   No autonomous merge/rollback.

## Approval boundary and proposed repair package

Nary approved this exact package at 2026-10-13 17:26:45 UTC. Approval covers
implementation, full checks, independent review, push/draft PR, exact-head CI,
merge, deployment, live verification and next-existing-cycle observation: support HK01 quote/table editorial blocks with
safe sanitization and order fixtures; validate/normalize public thumbnails and
reject non-image/default/malformed values before storage; diagnose HKEPC media
availability through its existing approved public interfaces without evading
Windows denials; remove verified translation numbering artifacts deterministically
without paid retranslation; distinguish API-body completeness from RSS excerpts;
and add the bounded media-QA/checkpoint stage described above to the existing
cycle. Include source-revision/cache invalidation tests, whole offline/browser
suite, exact-head draft PR CI and read-only live checks. This approval grants no future arbitrary code releases. If a source cannot supply media
through existing public interfaces, retain honest limitations rather than invent
replacement content.

Implementation notes: `tools/check_media_inventory.py docs` is a hard offline
prepublication gate. `tools/repair_media_snapshot.py` is a one-time, zero-provider
migration for three hash/ID-checked original captures, the confirmed numbering
artifact, same-article thumbnail normalization and API completeness labels. It
retains snapshot/fetch times and separately labels Mac capture/repair provenance.
It does not claim fresh Windows observations or regenerated analysis. Known
translation cleanup cannot fall through to paid cache-miss refresh.

Only a genuinely leading hero duplicate can be removed. An inline image after
editorial text stays in its original position; intentional source repeats remain.

Deployment and a successful next existing Windows run are still required. The
latest publisher queue was pending/queued during implementation; no manual
dispatch, cancellation or runner configuration change is authorized by this rule.
