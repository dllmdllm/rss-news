# Source/media audit — 2026-10-13

Audit executor: Mac. Branch `codex/rss-source-media-audit-20261013`. Configuration/publication pinned to `4ddd3b0fb8e5441d7bdbf6809e37a418d7a096b5`; snapshot `2026-10-13 16:39 HKT`. Public reads began `2026-10-13T12:22:20.063549+00:00`. No runtime code, schedule, credential, provider or delivery change was made. No push/merge/deploy or paid analysis was run.

## Coverage and evidence

- Config: **37/37** source feeds read once with existing public readers and empty paid keys. All returned current parsed entries on Mac; this is not a Windows availability test.
- Stored snapshot: **749/749** active articles and sidecars inventoried; all body hashes matched. Zero repeated IDs/URLs, all dates aware `+00:00`, descending UTC order, no future timestamps detected. **1969** stored inline image occurrences were inventoried. Byte-level metadata/index/build-status live comparison matches pinned revision.
- Original-page sample: one newest published article for **36/36** represented sources; **33** public HTML responses usable, **3** stopped on denial (法庭線/The Collective HK 403; HuffPost 生活 406). No bypass/retry. HuffPost 娛樂 has no distinct published row because its current one URL is already under HuffPost 新聞.
- Live UI: **36/36** sampled article sidecars exactly match pinned bytes; body text/image sequence after existing sanitization matches each sidecar. **57/57** sampled hero/body image loads succeeded. Every sample passed 390px overflow check. Images beyond the first three in a sampled article and other articles are **not load-verified**.
- Player: **722** cards, exact own-ID sentences; four thumbnail omissions are explained by existing safety guards (three relative TVB default paths, one malformed cnBeta field). **17** `duplicate_of` rows are filtered and **10** articles lack sentences (749−17−10=722). Actual source-filtered list/lead prefix and player agree in all three modes for all represented sources; HuffPost 娛樂 correctly renders an empty filter.
- Default all-source desktop list groups categories; player globally sorts. The initial naive concatenation comparison was corrected after code inspection and per-source checks. UTC/latest and score/balanced player ordering passed. Default grouped listing and player are intentionally not the same visual sequence.
- Existing applicable fixture/browser tests: **183 passed**. Strict current publication validator passed. Existing tests do not prove all live original articles are complete.

Original audit machine evidence (public metadata/hashes; later approved repairs are recorded separately): [source inventory](audit/source-media-20261013/source-audit.json), [live browser samples](audit/source-media-20261013/browser-audit.json), [source sort checks](audit/source-media-20261013/source-order.json), [embedded-original and issue details](audit/source-media-20261013/supplement.json), [original browser checks](audit/source-media-20261013/original-browser.json), [targeted issue UI](audit/source-media-20261013/targeted-issues-ui.json). Public-original HTML and fresh extraction captures are named by article ID in the same evidence folder.

## Current source matrix

`N`: every stored article inventoried; `T`: articles with thumbnails; `I`: articles with body images after intentional hero extraction. These counts do not certify image correctness/completeness. Each original/UI column concerns only one article per represented source. `rB`: rss-blocked; `n`: none. `DOM x/y` means literal normalized paragraph/caption matches, not complete extraction proof. Hydrated/translated sources need the separate notes below.

| Configured source | N / T / I | Stored provenance | Mac feed entries | Original sample | Live sampled image loads | Source-filter order | Notes |
|---|---:|---|---:|---|---|---|---|
| RTHK 本地 | 20 / 17 / 0 | n:20 | 20 | DOM 1/1 | 0/0 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article 9c06f0a24678](https://dllmdllm.github.io/rss-news/article.html?id=9c06f0a24678) |
| 明報 本地 | 30 / 29 / 0 | rB:30 | 30 | DOM 1/2 | 1/1 | PASS prefix ×3 | SOURCE-LIMITATION: all stored entries rss-blocked, original completeness not established; sample Mac page now readable. [article d2734fd03834](https://dllmdllm.github.io/rss-news/article.html?id=d2734fd03834) |
| am730 | 40 / 40 / 8 | n:40 | 40 | DOM 5/5 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article f6404ef31e32](https://dllmdllm.github.io/rss-news/article.html?id=f6404ef31e32) |
| 東網 本地 | 30 / 30 / 30 | n:30 | 30 | DOM 4/4 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article c346211a5d53](https://dllmdllm.github.io/rss-news/article.html?id=c346211a5d53) |
| 星島頭條 | 100 / 100 / 78 | n:100 | 100 | DOM 6/6 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article e6b33bf8cc05](https://dllmdllm.github.io/rss-news/article.html?id=e6b33bf8cc05) |
| RTHK 國際 | 20 / 20 / 0 | n:20 | 20 | DOM 1/1 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article c31a05cfdd68](https://dllmdllm.github.io/rss-news/article.html?id=c31a05cfdd68) |
| RTHK 大中華 | 12 / 11 / 0 | n:12 | 20 | DOM 1/1 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article 1f18f819fda7](https://dllmdllm.github.io/rss-news/article.html?id=1f18f819fda7) |
| 明報 國際 | 17 / 17 / 0 | rB:17 | 17 | DOM 3/3 | 1/1 | PASS prefix ×3 | SOURCE-LIMITATION: all stored entries rss-blocked, original completeness not established; sample Mac page now readable. [article 3e311741f597](https://dllmdllm.github.io/rss-news/article.html?id=3e311741f597) |
| 明報 中國 | 14 / 13 / 0 | rB:14 | 19 | DOM 2/2 | 1/1 | PASS prefix ×3 | SOURCE-LIMITATION: all stored entries rss-blocked, original completeness not established; sample Mac page now readable. [article 7530613df528](https://dllmdllm.github.io/rss-news/article.html?id=7530613df528) |
| 東網 國際 | 30 / 30 / 30 | n:30 | 30 | DOM 4/4 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article dc06a4ef1aa6](https://dllmdllm.github.io/rss-news/article.html?id=dc06a4ef1aa6) |
| 明報 娛樂 | 28 / 28 / 0 | rB:28 | 29 | DOM 3/3 | 1/1 | PASS prefix ×3 | SOURCE-LIMITATION: all stored entries rss-blocked, original completeness not established; sample Mac page now readable. [article 84fad0218658](https://dllmdllm.github.io/rss-news/article.html?id=84fad0218658) |
| 東網 娛樂 | 30 / 30 / 0 | n:30 | 30 | DOM 4/4 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article 5042a0075d54](https://dllmdllm.github.io/rss-news/article.html?id=5042a0075d54) |
| 明報 消閒 | 11 / 11 / 0 | rB:11 | 11 | DOM 2/2 | 1/1 | PASS prefix ×3 | SOURCE-LIMITATION: all stored entries rss-blocked, original completeness not established; sample Mac page now readable. [article 819dadceafbc](https://dllmdllm.github.io/rss-news/article.html?id=819dadceafbc) |
| WeekendHK | 10 / 10 / 4 | n:10 | 10 | DOM 4/4 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article 5f377f746185](https://dllmdllm.github.io/rss-news/article.html?id=5f377f746185) |
| GoTrip | 10 / 10 / 3 | n:10 | 10 | DOM 5/5 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article 131c830f745c](https://dllmdllm.github.io/rss-news/article.html?id=131c830f745c) |
| cnBeta | 45 / 45 / 24 | n:45 | 45 | DOM 0/47 | 1/1 | PASS prefix ×3 | FAIL malformed thumbnail on another article; repeated URLs in two articles. Simplified→Traditional sample paragraph match 50/51. [article caddbfd085f1](https://dllmdllm.github.io/rss-news/article.html?id=caddbfd085f1) |
| HKEPC | 10 / 0 / 0 | rB:10 | 10 | DOM 3/3 | 0/0 | PASS prefix ×3 | FAIL media availability: all 10 lack any image; original sample has article OG image and more content. Windows fallback limitation; no bypass. [article d1ccf0639b4f](https://dllmdllm.github.io/rss-news/article.html?id=d1ccf0639b4f) |
| Unwire | 10 / 10 / 6 | n:10 | 10 | DOM 16/16 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article 39d37045c482](https://dllmdllm.github.io/rss-news/article.html?id=39d37045c482) |
| Yahoo 科技 | 12 / 12 / 11 | n:12 | 10 | DOM 7/7 | 3/3 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article e31c9cb71132](https://dllmdllm.github.io/rss-news/article.html?id=e31c9cb71132) |
| 9to5Mac | 21 / 21 / 9 | n:21 | 22 | DOM 0/15 | 1/1 | PASS prefix ×3 | FAIL presentation: [0]… numbering leaked in one translated article; semantic full-translation fidelity not certified. [article f7bef5e0ede8](https://dllmdllm.github.io/rss-news/article.html?id=f7bef5e0ede8) |
| New MobileLife | 26 / 26 / 17 | n:26 | 27 | DOM 3/3 | 3/3 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article 205e173fca03](https://dllmdllm.github.io/rss-news/article.html?id=205e173fca03) |
| 法庭線 | 9 / 9 / 9 | rB:9 | 10 | DENIED HTTP 403 | 3/3 | PASS prefix ×3 | Production public API fresh 9; article-page 403, substantial fallback body present; full API/body fidelity not independently certified. [article edf3013227dc](https://dllmdllm.github.io/rss-news/article.html?id=edf3013227dc) |
| The Collective HK | 8 / 8 / 8 | rB:8 | 6 | DENIED HTTP 403 | 2/2 | PASS prefix ×3 | Production public API fresh 8; article-page 403; full API/body fidelity not independently certified. [article 9e0ceadde738](https://dllmdllm.github.io/rss-news/article.html?id=9e0ceadde738) |
| The Witness | 8 / 8 / 4 | n:8 | 6 | DOM 27/27 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article b8523dc846b0](https://dllmdllm.github.io/rss-news/article.html?id=b8523dc846b0) |
| HK01 突發 | 17 / 17 / 17 | n:17 | 17 | DOM 10/15 | 3/3 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article cf4f6608e919](https://dllmdllm.github.io/rss-news/article.html?id=cf4f6608e919) |
| HK01 社會 | 17 / 17 / 15 | n:17 | 18 | DOM 10/13 | 3/3 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article 55da158d8dca](https://dllmdllm.github.io/rss-news/article.html?id=55da158d8dca) |
| HK01 國際 | 20 / 20 / 20 | n:20 | 20 | DOM 57/96 | 3/3 | PASS prefix ×3 | FAIL completeness: 3 quote blocks absent. Embedded image order 41/41 matches. [article 47b9f09a024d](https://dllmdllm.github.io/rss-news/article.html?id=47b9f09a024d) |
| HK01 中國 | 10 / 10 / 10 | n:10 | 9 | DOM 12/16 | 3/3 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article 084ea1ca34f4](https://dllmdllm.github.io/rss-news/article.html?id=084ea1ca34f4) |
| HK01 娛樂 | 10 / 10 / 10 | n:10 | 10 | DOM 17/22 | 3/3 | PASS prefix ×3 | FAIL completeness: actor table absent. Images 22/24; full-gallery coverage unresolved. [article 6d37f02a65f7](https://dllmdllm.github.io/rss-news/article.html?id=6d37f02a65f7) |
| HK01 熱話 | 8 / 8 / 8 | n:8 | 9 | DOM 15/23 | 3/3 | PASS prefix ×3 | CURRENT DIFFERENCE: image order positions 0,2,1,4,5,6,3,7 vs current source; could be later source edit, historical raw capture absent. [article aae8eadf4948](https://dllmdllm.github.io/rss-news/article.html?id=aae8eadf4948) |
| HK01 深圳 | 9 / 9 / 9 | n:9 | 7 | DOM 37/80 | 3/3 | PASS prefix ×3 | Repeated exact image URL in one other article; source intentionality unresolved. [article 7b999b25c1af](https://dllmdllm.github.io/rss-news/article.html?id=7b999b25c1af) |
| HuffPost 新聞 | 15 / 15 / 4 | n:15 | 15 | DOM 0/8 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article 5d63dcc98442](https://dllmdllm.github.io/rss-news/article.html?id=5d63dcc98442) |
| HuffPost 娛樂 | 0 / 0 / 0 | — | 1 | No distinct row | UNVERIFIED | PASS prefix ×3 | PASS dedup explanation: one collected URL represented under HuffPost 新聞; no separate article/UI load coverage. |
| HuffPost 生活 | 2 / 2 / 2 | n:2 | 2 | DENIED HTTP 406 | 3/3 | PASS prefix ×3 | Article-page 406; live cached body/images pass sample; original fidelity unverified. [article 9aefb97b1b91](https://dllmdllm.github.io/rss-news/article.html?id=9aefb97b1b91) |
| TVB 新聞 | 30 / 30 / 12 | n:30 | 30 | DOM 1/1 | 1/1 | PASS prefix ×3 | FAIL thumbnails: 3 stored relative default paths; player safely omits them. [article 4710f1a09ff1](https://dllmdllm.github.io/rss-news/article.html?id=4710f1a09ff1) |
| Now 新聞 | 30 / 30 / 0 | n:30 | 30 | DOM 2/3 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article 94b14102c175](https://dllmdllm.github.io/rss-news/article.html?id=94b14102c175) |
| Now 國際 | 30 / 30 / 0 | n:30 | 30 | DOM 5/5 | 1/1 | PASS prefix ×3 | Original completeness beyond sample UNVERIFIED. [article abffc3812da8](https://dllmdllm.github.io/rss-news/article.html?id=abffc3812da8) |

## Confirmed findings and limits

1. **HK01 editorial block omission.** Current public embedded JSON for `47b9f09a024d` contains three quote messages (including 「這個法律體系已經辜負了我，這是我最後的手段。」); all three are absent from published text. `6d37f02a65f7` has an actor table (`blockType: table`, `htmlString`); published body has zero tables. Existing parser handles text/image/gallery/summary and skips these types. Current parser-gap proof is stronger than a low HTML substring coverage score. [Original](https://www.hk01.com/熱爆話題/60399041/落毒害同事全家-加州華裔工程師只判緩刑-受害者怒公開投毒影片) and embedded captures are available.
2. **Invalid thumbnail fields.** TVB IDs `7bd86660d030`, `f562146358be`, `9251b9591e6c` contain `/_nuxt/default.R189UE3B.jpg`; cnBeta `2b8e855b9e10` contains a malformed protocol-relative HTML fragment instead of an image URL. Player guard rejects these; article hero uses the same stored field and requires the targeted UI evidence. This is not an instruction to relax guards.
3. **HKEPC has no media in all 10 stored rows.** Newest stored body is a 221-character RSS fallback; current original includes a valid article OG image and visible article content. The live page honestly says RSS did not provide an image. Whether Windows can retrieve that media through its existing public interface remains unverified; successful Mac reads do not authorize an alternate access path.
4. **Translation output artifact.** 9to5Mac `f7bef5e0ede8` contains visible `[0]`, `[1]`… prefixes, matching translation input-unit numbering. Exactly one stored article has such leading numeric markers. No paid retranslation was attempted. English-source literal DOM match 0 is expected after translation and is not itself evidence of a missing body.
5. **Repeated image occurrences.** Four extra exact URL occurrences occur in three articles: cnBeta `2b8e855b9e10` (+2), `448b2d610abb` (+1); HK01 深圳 `1f9dae071acc` (+1). Repetition can be editorial; do not globally remove without source comparison.
6. **Current-source/body divergence.** HK01 熱話 `aae8eadf4948` has current-source image positions `[0,2,1,4,5,6,3,7]` in saved-body order. Other six HK01 sampled image lists are source-ordered subsequences. The snapshot predates the original read by several hours and no fetch-time raw-original checkpoint exists; classify this as difference requiring revision-aware refresh diagnosis, not proven historic parser reordering.
7. **No universal full-content certification.** 90 stored bodies are under 150 characters. This includes genuinely short reports and partial fallback bodies. All five 明報 feeds (100 total articles), HKEPC (10), and the two WordPress public-API feeds (17) have `rss-blocked` provenance. The latter can still contain rich public API body/media; a blanket “RSS excerpt” label is insufficient. Entire-source original fidelity, semantics of translated paragraphs, captions/tables outside samples and all image loads remain unverified.

## Production status and safety boundary

The current pinned generated data records successful Windows public-posts collection for 法庭線 (fresh 9) and The Collective HK (fresh 8), so the earlier “Windows source recovery pending” conclusion is superseded for this observed API collection. Their article HTML remains denied in this Mac audit. This does not certify perpetual source health or every API body.

Existing Windows [run 38370413281](https://github.com/dllmdllm/rss-news/actions/runs/38370413281) completed successfully and produced the current data publication. Later [run 38372756807](https://github.com/dllmdllm/rss-news/actions/runs/38372756807) failed in Fetch RSS, scrape & analyse before validation/push; public failed-step log retrieval returned “log not found”, so the precise cause is UNVERIFIED. [Run 38387147332](https://github.com/dllmdllm/rss-news/actions/runs/38387147332) was queued at inspection. The live snapshot is several hours old; retained live bytes match the pinned revision and are not fresh observations. No Windows command, cancellation, dispatch, runner or credential change was performed.

## Proposed next step needing one approval

Approve one focused repair-and-loop package, after final review: HK01 quote/table preservation; validated canonical thumbnail fields/default-image rejection; deterministic cleanup of verified translation markers; accurate public-API versus RSS/body completeness provenance; source-revision-aware media QA with deduplicated checkpoints, bounded sampling/backoff and existing-cycle recovery as specified in [SOURCE-MEDIA-QA.md](SOURCE-MEDIA-QA.md). Include HKEPC public-interface diagnosis and safe limitation handling, not denial bypass; diagnose existing publisher failure without changing runner settings or manually rerunning paid analysis.

Approval should explicitly cover implementation, full tests, branch push/draft PR, exact-head CI, merge and deploy of that reviewed package. No new schedule, provider, paid diagnostic rerun, recipient, token/cookie or access-control change is part of it. These audit documents alone grant no implementation or publication authority.

## Approved repair candidate

Nary approved the concrete repair/loop package at 2026-10-13 17:26:45 UTC.
The original matrix above remains a historical audit, not a post-release claim.
The candidate restores three ID/hash-checked original captures (HK01 quotes/table
and one HKEPC example), fixes four invalid heroes plus one URL canonicalization,
cleans the confirmed numbering artifact and labels 17 public-API body records.
All snapshot/fetch times remain unchanged; separate capture/repair provenance
prevents the Mac data repair from appearing as fresh Windows collection.

Candidate checks: 788 full offline/browser tests; worker security; JS syntax;
strict publication and all 749-record media gates; docs/cache/diff checks;
independent review with no remaining blockers. Local seven-article UI sampling
confirms quotes/table/image/marker/fallback/mobile behavior. A bounded six-source
public-reader profile completed in 15.21 seconds with 13 selected images decoded;
other original/image coverage remains unverified, as recorded in
[candidate observations](audit/source-media-20261013/candidate-six-source-qa.json).
[snapshot repair scope](audit/source-media-20261013/snapshot-repairs.json).

Exact-head PR CI, Pages/live verification and a successful next existing Windows
cycle are still rollout gates. Existing Windows publisher jobs were queued/pending
during implementation. No manual paid dispatch or runner configuration change.
