# Time-window collection and uninterrupted reading

The default feed, search and key-sentence player show the latest24hours. The
publisher retains its30hour overlap so a successful bounded batch cannot erase
previously seen news. Read Later controls are no longer displayed; existing
device saved/read/position records and article aliases are untouched.

| Behavior | Implementation and limits |
|---|---|
| Collection | All eligible records in a finite RSS/sitemap/index response survive validation. Existing public interfaces only; public DNS/TLS/redirect, response-byte and time guards remain. |
| Continuation | `sources[name].intake.resume` records published progress. Now uses official `pageNo`; Now/WordPress refresh page1 plus at most two older pages, then resume next cycle. WordPress retains15s total. Yahoo rotates at most the prior60metadata requests. Old/pinned head records do not end scanning. Missing pages, unknown coverage, ignored pagination and failed requests remain incomplete. |
| Bodies and paid AI | Prior per-source `max_items` becomes an admission/page-size budget, not eligibility. Cached work is reused; metadata above the body/title/translation/analysis budget stays collected with pending/partial status. Optional paid digests exclude deferred analysis rows. No paid quota or request-concurrency increase. |
| Device changes | Separate bounded local visit state compares stable IDs/aliases plus title/body revision. First visit is neutral; AI-summary-only changes do not mark a source revision. No external tracking and no reader-state deletion. |
| Evidence | Homepage AI-summary points and literal key sentences link separately to source evidence. The reader verifies the actual body SHA256 and a unique exact paragraph before highlighting it. Repeated/absent quotations, wrong versions and unavailable source remain explicit; partial bodies never claim full fidelity. |
| Groups | Existing explicit duplicate relationships produce expandable source articles, including non-root matches. Filtering precedes representative selection. All recorded members remain reachable, with nonmatching members labelled. Broad-topic association alone never merges events. |
| Refresh | Visible-page checks use the existing same-origin index, one in-flight8s/8MiB request,120s interval. New counts prompt the reader; only clicking applies. Search/filter/focus/caret and the current visible article anchor survive, including deep incremental lists. Pull-to-refresh checks for updates without reloading. |

Two diagnostic defects are repaired alongside this work. Yahoo Finance serves
header fields larger than aiohttp's8190byte default: all ten recorded failures
were parser errors; two unchanged official URLs returned200 with a finite64KiB
field allowance and the existing metadata parser accepted them. The allowance
is scoped to official Yahoo requests and leaves transport guards intact.

Translation cancellation previously lost1–4 completed results between five-item
flushes. Completed results now flush atomically after children drain, while
failed/cancelled results never become successful cache entries. Normal partial
failures return explicit counts/status. The previously timed-out32,770character
review is retained in full English; uncached translation inputs over8000chars
are explicitly deferred instead of issuing a97,839output-token monolith. No
provider request was made for this diagnosis or validation.

Schema2 metadata/index add the24h display/30h retention policy and projected
source evidence/status fields. The validator accepts the prior published index
while source-only rollout is pending; new writer output requires the entire
projection and all original metadata/sidecar/body hash checks. Service worker
v291 includes the functional modules and removal of Read Later controls. Layout
structure, AI entry points, stored reader data, existing schedules, PR27/PR3 and
subscription infrastructure remain. Mobile search retains the visible anchor
plus a result-page buffer during explicit refresh to prevent scroll clamping.

Coverage is limited by public source interfaces, finite requests/time/bytes and
normal scheduled progress. This is not an unlimited archive or a guarantee of
all publisher news. Native iPhone audio/keyboard and every image decode still
need actual-device/source checks. No new merge or deployment is implied by
branch tests or a draft PR; approval and exact public-byte verification remain
required under GIT-RUNBOOK.md and ROLLBACK.md.
