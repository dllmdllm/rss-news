<!-- ⚠️ 呢個檔案由 CLAUDE.md 自動生成，唔好直接改呢邊。
     改 CLAUDE.md，然後行：python tools/sync_agents_md.py
     tests/test_docs_sync.py 會 catch 兩邊唔一致。
     （2026-07-25 之前係人手同步，靜靜哋分岔咗六個星期。） -->

# rss-news — Project Guide

## 專案目標

純靜態 RSS 新聞聚合器，部署於 GitHub Pages。
抓取各新聞源的**完整文章內容（全文＋圖片，保留原始順序）**，生成靜態 HTML 頁面。

---

## 架構

```
rss-news/
├── src/
│   ├── fetch.py          # 並發抓取 RSS feeds（asyncio + aiohttp）；標題翻譯
│   ├── scrape.py         # 全文抓取：trafilatura + 各站自訂解析器
│   ├── analyse.py        # AI 分析：摘要 / 評分 / 標籤 / 情緒 / 話題（MiniMax）
│   ├── panel_digest.py   # 話題聚焦：共識 / 各媒體角度 / 張力（MiniMax）
│   ├── embed.py          # 語義向量：計算 embeddings → similar.json
│   ├── breaking_alert.py # 突發通知：Telegram bot 推送
│   ├── keyword_alert.py  # 慢速關鍵字通道：隨 build（~20 分鐘）比對 WATCH_KEYWORDS（+ trending）→ keyword_alerts.json + Telegram
│   ├── fast_watch.py     # 快速關鍵字通道：獨立 ubuntu workflow（5 分鐘），淨查 3 個最快 source 標題，唔碰 docs/data
│   ├── trends_watch.py   # Google Trends（香港）熱門字：隨 build（~20 分鐘）sync → trending_keywords.txt，自動並入關鍵字監控
│   ├── daily_brief.py    # 每日早報：HKT 06:00 後首個 build 綜合 24h top stories（MiniMax）→ daily_brief.json + Telegram
│   ├── entity_digest.py  # 實體摘要：聚合人物 / 機構 → entities.json
│   ├── minimax_client.py # MiniMax API thin wrapper（共用 HTTP shape + thinking 參數）
│   └── feeds.py          # RSS 來源定義及常數
├── config/
│   ├── watch_keywords.txt     # keyword_alert.py / fast_watch.py 共用嘅關鍵字清單，由 vault sync 寫，唔好手改
│   └── trending_keywords.txt  # Google Trends 香港熱門字，由 trends_watch.py 隨 build sync 寫，唔好手改
├── build.py              # 主程式：fetch → scrape → analyse → cluster → 輸出 JSON
├── docs/                 # GitHub Pages 根目錄
│   ├── index.html        # 文章列表頁（含 AI tab）
│   ├── article.html      # 文章閱讀頁
│   ├── js/
│   │   ├── index.js      # 列表頁邏輯（搜尋 / tab / AI 功能）
│   │   ├── article.js    # 文章閱讀邏輯（進度/swipe nav/next article）——TTS 同 key_sentences highlight 呢兩個之前有嘅功能喺 2026-05-23 redesign（`44fd85db47`）被移除咗，未重做
│   │   └── common.js     # 共用工具
│   ├── css/
│   │   └── categories.css  # 分類色彩（--cat-rgb / --cat-active-bg）
│   ├── sw.js             # Service Worker（network-first + stale-while-revalidate）
│   └── data/
│       ├── articles.json       # 所有文章（single source of truth）
│       ├── articles_index.json # 精簡索引（列表頁用）
│       ├── analyses.json       # AI 分析 cache（keyed by article id）
│       ├── panel_digests.json  # 話題聚焦 cache
│       ├── entities.json       # 實體摘要
│       ├── graph.json          # 知識圖譜（7 日）
│       ├── similar.json        # 相似文章對應表
│       ├── upcoming.json       # AI 預測事件
│       ├── embeddings.bin      # 向量數據（binary）
│       ├── embeddings_meta.json
│       ├── breaking_alerts.json
│       ├── keyword_alerts.json # keyword_alert.py 已推送記錄（dedup 用）
│       ├── daily_brief.json    # 今日早報（index 頁早報卡 + TTS）
│       ├── feed_http_cache.json  # HTTP 304 cache
│       └── content/            # 各文章完整 HTML（{id}.json）
├── CLAUDE.md             # 本文件（source of truth）
├── AGENTS.md             # CLAUDE.md 嘅鏡像，由 tools/sync_agents_md.py 生成
├── DESIGN-HISTORY.md     # 各條規則嘅案發經過（唔自動載入，要時先讀）
├── requirements.txt
└── .github/
    └── workflows/
        ├── update.yml     # GitHub Actions：每 20 分鐘執行 build.py（job timeout 25 min，self-hosted）
        ├── guardian.yml   # 斷更偵測 + wedge run 清理（ubuntu，每 30 分鐘）
        └── fast-watch.yml # 快速關鍵字通道（ubuntu，5 分鐘一次，00:00-07:00 HKT 除外）
```

---

## 數據流

```
RSS Feed
  └─→ fetch.py        抓標題 / URL / 日期 / 來源 / thumbnail；英文標題翻譯
        └─→ scrape.py     並發抓全文（trafilatura + 各站解析器）
              └─→ analyse.py    AI 分析（摘要 / score / tags / sentiment / topic）
                    └─→ build.py
                          ├─ detect_duplicates() → cluster_articles()
                          ├─ panel_digest.py  話題聚焦
                          ├─ embed.py         語義向量 + 相似文章
                          ├─ breaking_alert.py  Telegram 突發通知
                          ├─ keyword_alert.py   慢速關鍵字通道（同時 sync vault → config/watch_keywords.txt
                          │                      + trends_watch.py 隨 build sync → config/trending_keywords.txt）
                          ├─ entity_digest.py   實體摘要
                          └─ save_json()  → docs/data/

fast_watch.py（獨立 ubuntu workflow，5 分鐘一次，唔行上面條 pipeline）
  └─→ 淨查星島頭條 / am730 / TVB新聞標題 → 比對 config/watch_keywords.txt → Telegram
```

---

## 技術選型

| 用途 | 工具 | 原因 |
|---|---|---|
| RSS 解析 | `feedparser` | 穩定，格式相容性好 |
| 並發抓取 | `asyncio` + `aiohttp` | 同時抓多篇，速度快 |
| 全文解析 | `trafilatura` | 自動識別正文、保留圖片順序、抗噪聲強 |
| HTML 解析 | `beautifulsoup4` | 自定義元素展開、圖片修復 |
| 繁簡轉換 | `zhconv` | 簡體 → 香港繁體 |
| 標題翻譯 | MiniMax M2.7 | 英文 RSS 標題批量翻譯 |
| 反爬蟲繞過 | `cloudscraper` | 繞過 Cloudflare 驗證 |
| 環境變數 | `python-dotenv` | 讀取 .env（API Key）|
| 前端 | 純 HTML + Vanilla JS | 快，無框架開銷 |
| AI 分析 | MiniMax M2.7 | 摘要 / 評分 / 標籤 / 情緒 / 話題 |
| 語義搜尋 | `sentence-transformers` | 文章向量化，計算相似度 |
| 執行環境 | Python 3.13 | 最新穩定版 |

---

## RSS 來源

定義在 `src/feeds.py`（或 `src/fetch.py`）的 `RSS_FEEDS` 列表，格式：

```python
{"name": "來源名稱", "url": "RSS URL", "category": "分類"}
```

### 新聞（本地）
| 來源 | 備註 |
|---|---|
| RTHK 本地 | RSS ✓ |
| 明報即時 | RSS ✓；aiohttp 被封，需 urllib fallback |
| HK01 突發 / 社會 | 非 RSS，爬蟲 + `__NEXT_DATA__` 解析 |
| 東網 本地 | 非 RSS，自訂 DOM 解析器 |
| 星島頭條 | RSS ✓ |

### 國際
RTHK 國際 / 大中華、明報 國際 / 中國、東網 國際、星島 即時中國 / 國際、HK01 即時國際 / 中國

### 娛樂
明報 娛樂、東網 娛樂、星島 娛樂、HK01 娛樂

### 消閒
明報 消閒、WeekendHK、GoTrip

### 科技
cnBeta、HKEPC、Unwire、9to5Mac、New MobileLife、TVB News、Now News、
Yahoo 科技（`fetcher: yahoo`，2026-07-25 頂替 Engadget 中文）

### 網媒
法庭線、The Collective HK、香港法庭新聞

### 外媒（2026-07-30 新增）
HuffPost 新聞 / 娛樂 / 生活（`us-news` / `entertainment` / `life` section feed）。

同「國際」**特登分開**：國際係港媒寫嘅國際新聞，外媒係外國媒體自己嘅報道。
三個都喺 `ENGLISH_SOURCES`，標題同全文都會譯做香港繁體。純 trafilatura
抽到全文，唔使 custom parser。

⚠️ HuffPost 有啲 section feed 係 **HTTP 200 但 0 個 `<item>`**
（`front-page`、`style`，2026-07-30 實測）——揀 section 一定要真係數
`len(feedparser.parse(body).entries)`，見到 200 就當得就會種一個「靜默 0 篇」。

### 已移除來源（2026-07-25）
兩個都靜靜哋 0 篇超過一個月先發現（成因見「靜默 0 篇」一節）：

- **Engadget 中文** → 網站停運（DNS 都解析唔到），由 `Yahoo 科技` 頂上
- **SkyPost 要聞** → 晴報唔再出港聞，唔係 parser 壞。相關 code（約 370 行）
  已全部刪走，翻查睇 git history

⚠️ Yahoo 個 `/rss` 係空殼（0 個 `<item>`），一定要行 HTML fetcher；listing 頁
亦冇日期，要逐篇開文攞 `<time datetime>`（N+1 pattern，所以佢係現時最慢嘅 feed）。

→ 點解係「移除」而唔係「修」：[DESIGN-HISTORY.md](DESIGN-HISTORY.md#removed-sources)
---

## 開發階段

- **Phase 1（完成）** — 全文抓取 + 靜態頁面（列表頁 + 文章閱讀頁）
- **Phase 2（完成）** — AI 分析：摘要、重要性評分、標籤、情緒、話題 clustering
- **Phase 3（完成）** — Client-side 搜尋（Fuse.js，模糊匹配）
- **Phase 4（完成）** — AI tab：今日輿情（情緒分佈，2026-07-25 先真正做咗）、話題聚焦、各報講法有出入、事件時間軸、未來事件、熱門標籤、今日重點
- **Phase 5（完成）** — 知識圖譜、實體摘要、語義向量、突發通知

---

## 本地執行

```bash
pip install -r requirements.txt
cp .env.example .env        # 填入 MINIMAX_API_KEY
python build.py
# 輸出：docs/data/ 下所有 JSON 文件
```

---

## 自動化

GitHub Actions（`.github/workflows/update.yml`）每 20 分鐘執行一次 `build.py`：
- **job timeout：25 分鐘**（防止卡住無限等待；要預留網絡差嗰日 checkout 可以食 10 分鐘——2026-06-10 實測，16 分鐘上限令成朝零 push）
- 若 `docs/data/` 有變更則自動 commit & push（最多 retry 3 次 fetch/resync/push）
- **tests 喺 push 之後先跑**（2026-06-12 起）——test 係守 code regression，唔守 data；
  之前 test 行先，2026-05-31 一個爛 test 扣起文章 20 小時（73 個 run 連 fail）。
  Test fail 照樣令 run 轉紅＋通知，但文章已出咗街
- 每日第一個成功 run 發 Telegram heartbeat；失敗 run 發 Telegram 通知
  （**只喺由綠轉紅嗰下發一次**，連炒唔會洗版；持續斷更嘅提醒由 guardian 負責）

### Guardian（`.github/workflows/guardian.yml`）

跑喺 **GitHub-hosted ubuntu**（本機死咗都照行），每 30 分鐘（cron `7,37`）：
1. **Cancel 卡死 run**：update.yml 有 run `in_progress` 超過 40 分鐘即 cancel
   （job timeout 25 分鐘係 runner worker 自己執行，worker hang 咗冇人執法——
   2026-06-08 一個 run 卡 6h41m，後面 58 個 run 喺 queue 互相取代 14 小時）
2. **斷更偵測**：最新 data commit（`docs/data/articles.json`）超過 75 分鐘
   → 自動補 dispatch update.yml（兼治 GitHub cron 漏拍同 wedge 善後）
3. **告警去重**：由新鮮轉 stale 嗰下 Telegram 嗌一次（持續 stale 每 6 小時提一次），
   恢復時發 🟢。State 經 Actions cache（`guardian-state-*` key）傳遞

Secrets：`MINIMAX_API_KEY`、`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`

### Self-hosted runner（部署喺本機 Windows）

Workflow 跑喺 `windows-home` self-hosted runner（`C:\actions-runner`）。
「網站冇 update」十居其九係呢層出事，唔係 build.py 本身：

- **Runner**：已裝成 Windows service `actions.runner.dllmdllm-rss-news.windows-home`
  （2026-06-11 起，跑喺 **LocalSystem**，唔使 login 都會跑）。
  ⚠️ LocalSystem 見唔到 per-user 嘢：MS Store Python / user PATH / user pip cache
  全部唔存在。Workflow 嘅 Python 一定要用 all-users 安裝
  （`C:\Program Files\Python313`，python.org installer InstallAllUsers=1）。
  2026-06-11 切去 service 後正正係呢個位炒咗 80 個 run（舊 pin 指住
  `$env:LOCALAPPDATA\Microsoft\WindowsApps`，SYSTEM 下解析去 systemprofile）。
- **Watchdog / keeper（已退役）**：`rss-news-watchdog` 同 `rss-news-watchdog-keeper`
  task 已 Disabled（service 化之後冇存在價值）。Service 自帶 failure restart
  （5s/10s/30s，reset 86400——`sc.exe qfailure` 可查）。卡死 run 由 guardian
  workflow 負責 cancel，唔再靠本機 script。
- **Dispatch task**：`rss-news-dispatch`（每 20 分鐘）— GitHub 原生 cron 唔可靠，
  用 `gh workflow run` 補位。
- 診斷三步：`gh api repos/dllmdllm/rss-news/actions/runners`（offline?）→
  `Get-Process Runner.Listener`（死咗?）→ `C:\actions-runner\_diag\watchdog.log`
- Runner 長時間 offline 後，queue 入面嘅 run 可能 wedge（online 咗都唔執）：
  cancel 晒 stuck runs 再 `gh workflow run update.yml` 即可
- ~~治本選項~~（✅ 2026-06-11 已執行）：runner 已裝成 Windows service。
  診斷改用 `Get-Service actions.runner.*`；watchdog / keeper task 係 service 化
  之前嘅遺物，service 模式下唔再係主力

---

## Claude Code 設定

`.claude/`（gitignored，不入 repo）：

- **Hook：`hooks/block-env-edit.py`**（PreToolUse）— 擋 `Edit/Write/MultiEdit` on (1) `.env`（保護 `MINIMAX_API_KEY` / `TELEGRAM_BOT_TOKEN`），(2) 任何 `docs/data/` 之下嘅 generated artifact（`articles.json` / `analyses.json` / `embeddings.bin` etc. — 改源頭，唔好手改 output）
- **Hook：`hooks/pycompile-check.py`**（PostToolUse）— 改完 `.py` 即時跑 `python -m py_compile`，秒級 catch syntax / indent error，特別針對 `scrape.py` 嘅自訂 parser
- **Hook：`hooks/bump-sw-cache.py`**（PostToolUse）— 改完 `docs/index.html` / `docs/article.html` / `docs/js/**` / `docs/css/**` / `docs/vendor/**` / `docs/manifest.json` 後自動 bump `docs/sw.js` 嘅 `CACHE` version，迫 iOS Safari / SW 重抓新 asset（避免黑屏 / stale UI bug）
- **Hook：`hooks/run-related-test.py`**（PostToolUse）— 改完 source / frontend 即時跑對應 pytest（`src/<x>.py` → `tests/test_<x>.py`；`docs/js/*` / `docs/sw.js` / `docs/*.html` → `tests/test_frontend.py`；`build.py` → `tests/test_build.py`），90s timeout，失敗 surface 返畀 Claude 即時 fix
- **Skill：`skills/project-conventions/SKILL.md`** — 指返落面「設計決定」一節（唔再複製內容，見下面 ✅ 註）
- **Skill：`skills/add-feed-source/SKILL.md`**（user-invocable `/add-feed-source`）— 加新 RSS / 新聞源嘅 workflow：分類、parser 選型、feeds.py entry、smoke test、CLAUDE.md 同步
- **Skill：`skills/diagnose-feed/SKILL.md`**（user-invocable `/diagnose-feed`）— 單一 feed triage：跑 `diagnose.py <name-pattern>`，stage 化打印 fetch → scrape → preview（可選 `--analyse`），唔使等 15 min GH Actions cycle 就 pinpoint 邊個 stage 壞咗
- **Skill：`skills/release-frontend/SKILL.md`**（user-invocable `/release-frontend`）— 前端 release checklist：verify SW cache bump → `pytest tests/test_frontend.py` → vendor `?v=` token → conventional commit → push（注意 `update.yml` push event path filter 唔包 `docs/**`，前端 push 唔會自動 trigger workflow，要等 20 min cron 或手動 dispatch）
- **Subagent：`agents/async-timeout-reviewer.md`** — review 任何 timeout 改動（`asyncio.wait_for` / cloudscraper fallback / GH Actions `timeout-minutes`），確保三層 nested timeout 唔會錯位
- **Subagent：`agents/feed-parser-reviewer.md`** — review `src/scrape.py` 嘅 per-site parser 改動（`_build_hk01_content` / `_build_tvb_content` / `_NOWSNEWS_JUNK_RE` / `_add_featured_image` / cloudscraper fallback），catch silent regression（parser 返空 → fallback trafilatura → SEO preview only）
- **Global Skill：`andrej-karpathy-skills:karpathy-guidelines`**（user-scope plugin，唔喺本 repo 入面）— 寫 / review / refactor code 時應該套用，避免 overcomplication、做 surgical change、surface assumptions、define verifiable success criteria
- **MCP server：`context7`** — Library doc lookup（fetch 即時 React / aiohttp / trafilatura / feedparser docs，避免 hallucinate API）
- **MCP server：`github`** — GitHub repo / Actions / PR 操作（list workflow run、download log、re-dispatch、status check）
- **`settings.local.json`** — 個人 permission allowlist + hook 設定（PreToolUse + PostToolUse）

✅ 2026-07-25：`project-conventions` skill 由「設計決定」嘅逐字副本（276 行、
16 個章節全部重複）改成幾行指路。**本文件係唯一來源，冇同步負擔。**
原因：CLAUDE.md 每個 session 都自動載入，個 skill 一觸發就會喺已經有嗰份之上
再疊多一份，即係 Anthropic 講嘅 over-constraining（[context engineering
rules](https://claude.com/blog/the-new-rules-of-context-engineering-for-claude-5-generation-models)）。
**加新設計決定寫喺下面就得，唔好再開副本。**

📄 **文檔分工**（同上面同一個道理——只放每次都要嘅嘢喺自動載入嗰份）：

| 檔案 | 幾時入 context | 放乜 |
|---|---|---|
| `CLAUDE.md` | **每個 session** | 規則、invariant、⚠️ 陷阱 |
| `DESIGN-HISTORY.md` | 手動 Read | 案發經過、實測數字、試過唔得嘅方向 |
| `AGENTS.md` | Codex 等讀 | CLAUDE.md 嘅完整鏡像 |

- 寫新嘢之前問一句：**「下次改 code 嗰陣要唔要知？」**要 → CLAUDE.md；
  「係咪真係要咁做」先要 → `DESIGN-HISTORY.md`，喺 CLAUDE.md 留條連結
- **AGENTS.md 唔好手改**：`python tools/sync_agents_md.py` 生成
  （`--check` 淨係驗唔寫）。`tests/test_docs_sync.py` 會 catch 兩邊唔一致、
  同 CLAUDE.md 指去 `DESIGN-HISTORY.md` 但 anchor 唔存在。
  之前人手同步，靜靜哋分岔咗六個星期先發現

---

## 設計決定（勿輕易修改）

以下係經過 debug 確認的非顯而易見決定，修改前請先了解原因。

### MiniMax API 接入方式

`src/analyse.py` 使用 **Anthropic-compatible endpoint**，**不是** MiniMax 原生 API：

```
POST https://api.minimax.io/anthropic/v1/messages
Header: x-api-key: <MINIMAX_API_KEY>        ← 不是 Bearer，不是 Authorization
Header: anthropic-version: 2023-06-01
```

- 不需要 GroupId、不需要 Bearer token
- Response 格式：`data["content"][0]["text"]`（要篩 `type == "text"` 嘅 block，因為 M2.7 會前置 thinking block）
- 錯誤碼 `overloaded_error`（529）需 retry，10s/20s backoff
- Rate limit 1002 → 調低 `ANALYSE_CONCURRENCY`（目前 5，安全上限約 500 RPM）
- **thinking 參數**：所有 structured-output 調用（translate / analyse / panel / entity /
  daily_brief）都傳 `thinking={"type": "disabled"}`——reasoning tokens 會食
  `max_tokens` 令 JSON 答案被截斷。M2.7：呢個參數接受但無視，thinking 本身冧唔到、永遠
  開住（2026-06-10 probe 確認）。**M3**（官方文件 2026-07 核實）：預設**關**，
  `disabled` 明確保持關閉、`adaptive` 先會開——同之前 CLAUDE.md 記錄嘅「M3 預設開」
  方向相反（可能係 preview 階段改咗預設值），但兩個方向代碼都安全，因為已經明確傳
  `disabled`，唔使因為呢次修正而改 code
- **遷移去 M3**：淨係改 `MINIMAX_MODEL` 環境變數（`.env` / GitHub secret），四個
  AI 模組零改動；已用真實 API A/B 測試過 analyse 嘅 prompt，M3 輸出格式正確、
  用字更穩（M2.7 出過簡體字「地区」漏網）。Pricing 兩個 model 一樣（$0.30/$1.20
  每百萬 input/output tokens，M3 淨係輸入超過 512K tokens 先加價，遠超呢個 project
  用量）。2026-07-13 已切換

### scrape 超時架構

`scrape_all()` 設有 **4 分鐘總超時**（`asyncio.wait_for(..., timeout=240)`）：

```python
results = await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), timeout=240)
```

原因：`cloudscraper` 在 thread pool executor 執行，即使設 HTTP timeout，底層 TLS/CAPTCHA
邏輯仍可能無限掛起。`asyncio.wait_for` 不能殺線程，但能讓 event loop 繼續，避免整個
build 卡死。超時後用舊有 content 繼續後續步驟。

`cloudscraper` 呼叫額外包一層 `asyncio.wait_for`（`_FALLBACK_TIMEOUT + 5`）。

### build.py 全局超時

```python
asyncio.run(asyncio.wait_for(main(), timeout=850))  # ~14 分鐘
```

配合 workflow `timeout-minutes: 25`，確保 job 不會無限運行（25 嘅原因見 update.yml 註釋：慢網日 checkout 可食 10 分鐘）。

`compute_embeddings`（`src/embed.py`）2026-07-21 起改做 async，內部用
`asyncio.create_subprocess_exec` 起 `python -m src.embed_worker` 跑真正
嘅 sentence-transformers 計算（`_compute_embeddings_sync`），逾時
`proc.kill()`。之前用 `loop.run_in_executor` + `wait_for` 有個死角：
cancel 個 wait_for 淨係停止等待，唔會真正殺咗個 thread（Python 冇 API
殺 thread），而且 `asyncio.run()` 自己收尾嗰陣（`shutdown_default_executor`）
會等呢條背景 thread 行完先返，一 hang 就算全局 `wait_for(850)` 都救唔到，
成個 `python build.py` process 卡住。Subprocess 可以真.SIGKILL，冇呢個
問題。

### lxml 會 segfault 成個 process（`faulthandler` + 快失敗 retry）

`build.py` 開頭一定要保留 `faulthandler.enable()`。lxml 個 C extension
（`etree.cp313-win_amd64.pyd`）會喺 scrape 中途 access violation
（`0xC0000005`）殺死成個 interpreter——2026-07-20→30 中咗 11 次，約 1.5% build。

⚠️ **呢種死法喺 Actions log 係完全隱形**：native crash 唔會 unwind 上 Python
（冇 traceback），而 stdout 喺 pipe 底下 block-buffered，process 被 OS 殺嗰下
buffer 一齊冇——log 得返一句 `exit code 1`，零 output。見到呢個 pattern
（~12 秒、冇 output、exit 1）**唔好去 debug build.py 邏輯**，去查本機
Windows Application event log（`Get-WinEvent -ProviderName 'Application Error'`）。
`faulthandler` 就係為咗令下次唔使再咁查。

`update.yml` 個 build step 快失敗 retry 一次。⚠️ **判斷條件係 elapsed time
（<420s）而唔係 exit code**——build.py 自己 850s 逾時嗰陣一樣係 exit 1，
retry 嗰個會爆 `timeout-minutes: 25`。

→ 案發經過、點解未改 scrape parsing 路徑：[DESIGN-HISTORY.md](DESIGN-HISTORY.md#lxml-crash)

### fetch per-feed 超時

`fetch_all()` 每個 feed 包一層 `asyncio.wait_for(75s)`。冇呢層嘅話，
單一 feed 卡死（而家最慢係 Yahoo 科技，要逐篇開文攞日期）會食晒 build.py 嘅 150s fetch
budget，**全部來源**都 fallback 去舊文章；有咗就只係嗰個 feed 降級。

### HK01 全文抓取（`_build_hk01_content`）

HK01 係 Next.js app，article body 全靠 client-side hydration，trafilatura 只能見到
SEO 預渲染的少量文字。從 `__NEXT_DATA__` 抽出完整內容：

- 路徑：`props.initialProps.pageProps.article`
- **第一段在 `article.description`**，不在 `blocks` 內（blocks 從第二段開始）
- blocks 結構：`summary`（字串陣列）、`text`（`htmlTokens` 列表之列表）、`image`、`gallery`

```python
# 必須先 prepend description
parts.append(f"<p>{_html_escape(description)}</p>")
# 然後才處理 blocks
```

### TVB News 全文抓取（`_build_tvb_content`）

TVB News 係 Next.js app，內容在 `__NEXT_DATA__`：

- 路徑：`props.pageProps.newsItems`（直接是文章 dict，不是 nested）
- 文章正文：`newsItems.desc`（純文字，`\n` 分段）
- 圖片：`newsItems.media.image`（list，`default: true` 的為主圖，用 `big` URL）

trafilatura 在 TVB 只能抽出「繁简 無相關新聞內容」，必須用自訂解析器。

### Yahoo 科技全文抓取（`_build_yahoo_content`）

Yahoo 嘅「其他人也在看」欄**嵌住成篇完整推薦文章**（唔止連結），所以 trafilatura
會將完全無關嘅新聞掃埋入正文，再餵落 `analyse.py` 污染摘要／標籤／topic。

鎖死最窄嘅純內文容器：

```python
_YAHOO_BODY_SELECTOR = "section.module-article-body div.atoms"
```

`div.atoms` 以外全部係 chrome（麵包屑、重複標題、byline、出版商 logo、推薦欄）。
入面仲有「廣告」spacer 段落，用 `_YAHOO_AD_MARKER_RE` 濾走。

⚠️ **驗 parser 要讀返抽到嘅文字，唔可以淨係量長度**——垃圾正正係撐大字數嗰樣嘢，
所以污染咗嘅版本反而「睇落健康」。乾淨版每篇得 500-1,800 字（多數係短快訊）。

→ DOM 調查數據同當時個判斷失誤：[DESIGN-HISTORY.md](DESIGN-HISTORY.md#yahoo-body)

### NowsNews 瀏覽器兼容提示

Now News 頁面含瀏覽器兼容提示，trafilatura 會抽出：

```
抱歉，我們並不支援你正使用的瀏覽器。為達至最佳瀏覽效果...
```

在 `_process_html_sync` 後用 `_NOWSNEWS_JUNK_RE` regex 過濾。

### `_add_featured_image` 插入位置

縮圖必須插入 `<body>` **內部**，而不是字串最前：

```python
# 正確
content.replace('<body>', f'<body>{img}', 1)
# 錯誤（img 被 innerHTML 忽略）
img + content
```

原因：`BeautifulSoup` 會將 HTML fragment 包成完整 `<html><head></head><body>…</body></html>`
結構，`<img>` 若在 `<html>` 之前，瀏覽器 `innerHTML` 賦值時會忽略。

### update.yml PowerShell step 引號鐵律

Runner 係 Windows PowerShell 5.1。native 指令（gh/curl）嘅參數**唔好用內嵌雙引號**
（bash 式 `\"` 會被 PS 斬開參數；經變數傳入嘅內層引號會被 C runtime 剝走——
run #5664 一役兩種寫法都中）。要過濾／計數就攞 JSON 返嚟用 `ConvertFrom-Json` +
`Where-Object` 做；send Telegram 要 `-Body ([Text.Encoding]::UTF8.GetBytes($body))`，
PS 5.1 對 string body 硬編碼 ISO-8859-1（charset 聲明冇用，❌ 曾變 âŒ）。

### GitHub Actions push 策略

每次 retry 都先重新 fetch/rebase，避免 concurrent push 導致 rejected：

```bash
git fetch origin main
git rebase origin/main -X ours
git push origin HEAD:main
```

`-X ours` 只在 rebase 衝突時保留本次新生成的 `docs/data/` 內容，不是 force push。

### 圖片 hotlink 保護

`docs/article.html` 所有 `<img>` 設有：

```javascript
img.referrerPolicy = "no-referrer";
```

明報、RTHK 等驗證 Referer header，`no-referrer` 可繞過。
`<meta name="referrer" content="no-referrer">` 亦在 `<head>` 作雙重保障。

### analyses.json cache 結構

```json
{
  "article_id_12char": {
    "summary": "・重點1\n・重點2",
    "score": 8,
    "tags": ["標籤A", "標籤B"],
    "sentiment": "negative",
    "topic": "標準化話題名稱",
    "key_sentences": ["句子1", "句子2"]
  }
}
```

- `score: null` → migrate 過來的舊 entry，下次 build 重新分析
- `topic` 用於 `cluster_articles()`：相同 topic 歸為一組
- **散文式 summary 拒收**（`_summary_looks_acceptable()`，2026-07-22）：
  SYSTEM_PROMPT 要求 5-8 個 ・bullet，但 MiniMax 偶爾會吐返成段散文（HP
  BIOS 文章 `1c81d678187d` 個案：116 字單行、冇 ・冇 \n）。≥40 字而又
  完全冇 ・冇 \n 就當 parse 失敗——`_normalise_parsed()` 拒收觸發 retry，
  `_needs_full_analysis()` 令已 cache 嘅散文 entry 下次 build 重新分析。
  40 字長度 gate 防止誤殺短 placeholder（測試用嘅 "x" 呢類）
- `key_sentences`：文章閱讀頁 2026-05-23 redesign 之前有做過 substring
  highlight，而家淨係喺 article.js 打做「關鍵句」plain list（`docs/js/index.js`
  嘅摘要拼接都有用到）。translate_content.py 一定要行喺 analyse.py 之前嘅
  ordering guarantee（保持 key_sentences 同 content 用字一致）依然有效、
  依然值得保留——即使宜家個 highlight consumer 唔存在，呢個 invariant
  本身零成本，之後想重做 highlight 都唔使再理呢層

### Mobile view class 命名（`docs/index.html`）

Body 嘅 view state class 係 `mobile-${view}`（`mobile-home` / `mobile-ai` / `mobile-search` /
`mobile-settings`），所以**設定面板嘅 class 一定要係 `.mobile-settings-panel`，唔可以係
`.mobile-settings`**——settings view 時 body 都有 `mobile-settings` token，一條 bare
`.mobile-settings { display: none }` 會連 body 一齊藏，成頁黑屏兼經 localStorage persist
（2026-07-12 修復；之前一直誤當 SW stale cache 醫）。Inline script 亦只會喺
`max-width: 900px` 先加 mobile class，desktop 唔受舊 localStorage 影響。

### articles_index.json 係 homepage payload

`docs/js/index.js` 讀 `articles_index.json`（slim：冇 key_sentences / entities / url），
schema 唔齊（舊 build）會 fallback `articles.json`。改 build.py 嗰段 index_payload 時
記住 index.js 嘅 `payloadIsComplete()` 檢查 `summary` + `sources` + `trending_topics`。
兩頁 fetch 都改咗用 `cache: "no-cache"`（ETag 304），唔好加返 `?${Date.now()}`。

### 測試唔可以寫真 docs/data/

`src/panel_digest.py` / `src/entity_digest.py` / `src/breaking_alert.py` / `src/keyword_alert.py`
各自有 module-level 絕對輸出路徑（唔跟 `build.DATA_DIR`）。任何跑 `build.main()` 嘅 test
必須 monkeypatch 晒呢啲路徑，否則 dry run 會改寫真數據（試過 wipe `panel_digests.json`；
2026-07-20 `keyword_alert.VAULT_PATH`/`CONFIG_PATH` 都中過一次，見下）。

### 關鍵字監控：雙通道 + vault sync（`src/keyword_alert.py` / `src/fast_watch.py`）

兩條 Telegram 通道，共用同一份關鍵字清單：

- **慢速**（`keyword_alert.py`）：隨主 build，比對全 source 嘅 title/summary/tags。
  ⚠️ **2026-07-22 起停用**——`build.py` 嘅 `SLOW_KEYWORD_ALERTS_ENABLED = False`；
  module 同 test 完整保留，一行 flip 就返到轉頭
- **快速**（`fast_watch.py`）：獨立 `fast-watch.yml`（ubuntu，5 分鐘，00:00-07:00
  HKT 除外），淨查星島頭條/am730/TVB新聞嘅標題，唔 scrape 唔叫 AI。
  **刻意唔碰 `docs/data`、唔 git push**（避免同主 build 撞）。慢速停用後，
  **呢條係唯一活躍嘅通道**

**清單來源**：vault（`RSS News - Watch Keywords.md`）→
`sync_watch_keywords_from_vault()` → `config/watch_keywords.txt`。
⚠️ 呢個 config **同 `trending_keywords.txt` 都要喺 `update.yml` 嘅
`stage_outputs()` 入面**（快速通道喺 ubuntu 見唔到 vault，靠 checkout 攞）。
⚠️ Vault note **一定要有 `## 關鍵字清單` 標題**，搵唔到就整個拒絕同步（fail-closed，
避免將說明文字當關鍵字寫入）。

**Match 規則**：字面 substring、唔分大小寫、OR——"OpenAI" 唔會 match "ChatGPT"，
中英對照/品牌/人名要列晒。

**`context:` directive**：關鍵字前面一行 `context: 港人, 本港, 本地, 香港`，令之後
每個字都要連同其中一個 context word 一齊出現先算 match；單獨一行 `context:` 解除。
Parse 喺 `_parse_keyword_rules()` → `WATCH_KEYWORDS` + `KEYWORD_CONTEXT`，兩條通道
嘅 match function 都食呢個 dict。現套用喺交通意外組／死亡組／自殺組／暴雨組。

**去重**：慢速用 AI `cluster_id`（同 cluster 揀最新一篇）＋ keyword cooldown
（`KEYWORD_COOLDOWN_MINUTES=30`）；快速冇 clustering，淨用 cooldown。
⚠️ **Cooldown 一定要逐篇 check + send 完即時更新**，唔可以喺 loop 之前一次過計
eligible list（試過一個 run 內 3 篇同 keyword 齊齊送晒）。

⚠️ **快速通道嘅 `seen` 唔可以淨靠 article id**（2026-07-25）：id 係 `md5(url)`，
而好多 source 個 url 帶住分類同標題 slug（am730 個樣係
`/財經/1043642/黃仁勳開x帳號談ai-...`）。網站改分類、執下標題就換咗 md5，
成篇文喺 `seen` 眼中「復活」再 alert 一次——實際中過（同一篇 am730 文 12:02
同 13:02 各推一次；查過 cache 鏈完整、`seen` 由 1918 升到 1934，唔關 state
遺失事）。`_seen_keys()` 而家同時記 `id` 同 `source|標題`。**兩個都要記**，
唔係 deploy 嗰下舊 state 只有 md5 id，窗口內全部文會一次過當新文洗版。
Now 新聞 換 `newsId` 重發同一篇都係同一個病（實測 articles.json 有 5 組）。

⚠️ **「5 分鐘」係輪詢間隔，唔係端到端延遲**：實測一篇 am730 文標示 11:15
出街，但 11:17–11:57 每 5 分鐘一次嘅 run 全部見唔到，12:02 先 match 到——
即係篇文到 ~12:00 先入到 am730 個 sitemap。源頭幾時放出嚟控制唔到，
workflow 再密都冇用。

**Google Trends**（`src/trends_watch.py`）：`sync_trending_keywords()` 跟 build 同
頻率 fetch，**每次完全覆寫**（唔累積）。`MIN_KEYWORD_LEN=2` 濾走單字；標題經
`zhconv` 轉香港繁體（唔轉就永遠 match 唔中繁體內文）。兩條通道合併
`WATCH_KEYWORDS` + trending 先 match；trending 字冇喺 `KEYWORD_CONTEXT` 登記，
所以自動當冇 context 限制。

**Trending 獨立節流**：`TRENDING_COOLDOWN_MINUTES=90`（curated 用 30）+ 細 quota
（`MAX_TRENDING_ALERTS_PER_BUILD=2` / `PER_RUN=1`），免得日常熱話擠走人手揀嘅字。
判斷「係咪 trending」：喺 trending 清單但唔喺 `WATCH_KEYWORDS`（兩邊都有當 curated）。
訊息會加 ` (From Google Trend)` 標籤。

⚠️ **Test isolation**：任何用到關鍵字嘅 test 都要 monkeypatch
`load_trending_keywords()`（`keyword_alert`）/ `TRENDING_KEYWORDS`（`fast_watch`，
module-level）同 `KEYWORD_CONTEXT`，否則會撞中真實 synced config。兩個 test file
已有 autouse fixture。

→ 點解會演變成咁（五次改動嘅來龍去脈、試過唔得嘅方向）：
[DESIGN-HISTORY.md](DESIGN-HISTORY.md#keyword-watch)

### 靜默 0 篇：來源斷更偵測（`src/source_health.py`）

Fetcher 攞到 0 篇但返 `error=None` → build 全綠、冇人知個 source 死咗。
**已中三次**（東網娛樂、SkyPost、Engadget，後兩個死咗一個月先發現）。

`check_source_health()` 每次 build 尾段跑，將各 source 嘅 `effective_count`
摺入 `docs/data/source_health.json`：

- 連續 `ZERO_ALERT_AFTER_HOURS`（24h）都係 0 先當斷更（單次 0 篇太常見：
  上游斷線／深夜冇新聞／HTTP 304）
- 告警跟 `guardian.yml` 形狀：轉態嗌一次 🟠、恢復 🟢、持續死每
  `REMIND_EVERY_HOURS` 先提一次
- `evaluate_sources()` 係純函數（收 `now`/`state`，冇 I/O），test 可餵假時鐘

⚠️ `source_health.json` **一定要入 `update.yml` 嘅 `stage_outputs()`**——唔 stage
就每次 checkout 清零，24h 門檻永遠夠唔到，機制等於冇（同 `daily_brief.json` /
`translated_content.json` 中過嘅伏一樣）。Test 要 monkeypatch `STATE_PATH` +
`TELEGRAM_BOT_TOKEN`，見「測試唔可以寫真 docs/data/」。

→ 三次案例同點解門檻要 24h：[DESIGN-HISTORY.md](DESIGN-HISTORY.md#silent-zero)

### GitHub cron 唔可靠：兩個 workflow 都要本機 dispatch 補位

GitHub 對高頻 cron throttle 得好犀利——`fast-watch.yml` 寫住 `*/5`，實測真身係
1.5–8 小時一次。兩個 workflow 而家都由本機 Task Scheduler 推：

| Task | 頻率 | Script | Workflow |
|---|---|---|---|
| `rss-news-dispatch` | 20 分鐘 | `C:\actions-runner\dispatch-update.ps1` | `update.yml` |
| `rss-news-fast-dispatch` | 5 分鐘 | `C:\actions-runner\dispatch-fast-watch.ps1` | `fast-watch.yml` |

兩個 script 都有 race guard：見到有 run `queued`/`in_progress` 就唔 dispatch
（第二個 dispatch 會 cancel 緊行緊嗰個）。

⚠️ `fast_watch.py` **本身冇 quiet-hours 檢查**，00:00-07:00 HKT 唔嘈全靠 cron 個
`23,0-15 UTC` 時段擋住——所以 `dispatch-fast-watch.ps1` 自己重複咗一次呢個判斷。
手動 dispatch 或者改 script 前記住呢層，唔係會半夜彈 Telegram。

→ 實測數字：[DESIGN-HISTORY.md](DESIGN-HISTORY.md#github-cron)

### AI tab：唔好淨係堆「排序過嘅新聞清單」

2026-07-25 用戶反映 AI tab「好似唔係太 AI」。review 揾到根因：佔最大面積嘅
兩格（🔥 優先排行、今日 AI 摘要）本質上係**同一批文章換個次序再列一次**，
無論背後個 score 幾聰明，睇落都係普通新聞清單。真正 AI-native 嘅輸出當時
generate 咗但冇出街：

| 欄位 | 當時狀況 |
|---|---|
| `tension`（分歧／缺口） | 7/7 topic 都有，前端 **0 次讀** |
| `timeline`（事件時間軸） | 5/7 topic 都有，前端 **0 次讀** |
| `sentiment` | 每篇都有，首頁 **0 次讀**（只喺文章頁打一行純文字） |
| `similar.json` | 每 build 燒 13.7s 計，**全站冇人讀** |

已加返 🧭 今日輿情（情緒分佈條，可撳落去篩）、📈 事件時間軸，並將
`tension` 併入「⚡ 各報講法有出入」——原本嗰格淨靠 `contradictions`
（得 3/7 topic 有）成日空白，加咗 tension 之後長期有嘢睇。

⚠️ `renderMood()` 要用**全量** `state.articles` 而唔係 filtered list——
否則撳咗「負面」之後條分佈變 100% 負面，睇落好似壞咗。
✅ `similar.json` 2026-07-25 起接返落文章頁「相關新聞」：`relatedArticles()`
優先食 similar map（cosine top-5），揀唔夠先用返舊嘅 tag/topic heuristic 補位。
實測同一單新聞嘅多媒體報道，語義版 4/4 全中，heuristic 有一條係靠「同 source
同分類」撈返嚟嘅無關文。fetch 失敗就靜靜降級用 heuristic，唔可以變空白。

**教訓**：加新 AI 功能之前先問「呢個喺 UI 邊度出現？」——呢個 project
一度有 4 個欄位係 generate 咗、燒咗 token／CPU，但從來冇人見過。

`entities.json` / `graph.json` **唔屬於呢類**：佢哋有專屬頁（`entities.html` /
`graph.html`），只係入口收喺 AI 欄底部一行細連結，屬「入口太隱蔽」而唔係「冇人讀」。
2026-07-25 起 entities 有一格 🏷️ 今日焦點入咗 rail（每類最多 2 個，撳落去用
`article_ids` 篩文——唔好夾實體名，AI 抽到嘅名唔一定逐字出現喺標題/摘要）。

**`graph.json` 特登唔入 rail**，唔係漏咗：
- top 12 連結有 8 條係「人物 ←→ 佢自己個地方」（天文台↔香港、特朗普↔美國），
  資訊量低
- 48 KB，而首頁本身已經載 451 KB
- force-directed 圖喺 320px 闊嘅欄基本上撳唔到（cytoscape 過 150 節點仲會 lag）

### 實體別名合併（`entity_digest.ENTITY_ALIASES`）

同一個實體俾 AI 抽成幾個名，count 被拆散——「天文台」21 篇 +「香港天文台」
13 篇，合埋 34 先係真相。`canonical_entity(etype, name)` 做簡繁 normalize +
查表合併，`entity_digest.py` 同 `build.py` 個 graph builder **兩邊都要 call**
（2026-07-25 之前 graph builder 用 raw name、連 zhconv 都冇，所以「李慧琼」
同「李慧瓊」係兩個節點，同 entities.json 對唔上）。

⚠️ **一定要人手維護張表，唔可以用 substring 自動 merge。** 真數據反例：

```
「東京都」contains「京都」          ← 兩個唔同城市
「天文台」contains「歐洲南方天文台」
「香港」contains「香港會議展覽中心」  ← 唔同粒度
```

地點只合併「同一地方嘅寫法差異」，唔合併粒度差異——「廣東東部」唔會 merge
落「廣東」，因為打風報道講嘅正正係東部沿岸。`ENTITY_MIN_ARTICLES = 3` 已經
濾走長尾，所以只需要處理「合併之後會影響排名」嗰幾個。

### 文章頁：關鍵句擺位

關鍵句（`key_sentences`，AI 由原文逐字摘錄嘅 3-5 句）2026-07-25 由右欄
`<aside class="ai">` 搬去主欄 `summaryBox` 下面——同 AI 摘要一樣都係「睇正文
之前想知嘅嘢」，之前擺喺右欄要撳個 AI 掣展開先見到。

⚠️ `.fact-list li` **唔可以有 `-webkit-line-clamp`**。喺右欄嗰陣為咗夾窄闊度
clamp 咗 2 行，但關鍵句每句 10-80 字，長啲嘅直接變「……」——用戶睇唔到重點
先反映。搬咗落主欄有闊度，完整顯示。冇關鍵句就 `$("keyFacts").hidden = true`，
唔好喺摘要同正文之間留個空框。

### 手機 AI tab 三個分頁

`mobile-sub-ai` 有三個 mode：`priority` / `category` / `analysis`。
之前得頭兩個，而分析欄（`.ai`）係硬疊喺清單下面一路捲落去，一頁好長又分唔清。
而家 `analysis` 顯示 `.ai` 收起 `.main`，另外兩個相反——靠 `body.ai-mode-analysis`
呢個 class 二選一（CSS 喺 `index.html` 嘅 `@media (max-width: 900px)` 入面，
class 由 `updateMobileSubUi()` toggle）。
`analysis` 同 `priority` 一樣唔帶 `aiCat`/`aiSource` 篩選（見 `syncStateFromMobile()`）。

⚠️ **三個分頁一定要各有各睇。** `.brief` 入面有兩截（`.brief-priority` 優先排行
／`.brief-category` 今日AI摘要），2026-07-25 之前兩個 tab 都 render 晒成個
`.brief`，所以「優先」同「分類重點」頂部都係優先排行，用戶反映「好混亂」。
而家靠 `body.ai-mode-priority` / `body.ai-mode-category` 二選一。
Desktop 唔受影響（兩截照樣一齊出）。

### 加一個新分類要改九個位

分類名散落喺九個地方，**漏咗任何一個都唔會報錯**，只會靜靜哋冇咗個掣／冇咗
顏色／喺 graph 度俾 whitelist 濾走：

| 檔案 | 位置 |
|---|---|
| `src/feeds.py` | feed entry 個 `category` |
| `docs/js/index.js` | `categories`、`categoryEmoji`、`categoryClass`、`SORT_CATEGORY_ORDER`、`CATEGORY_GROUPS`（**五個**） |
| `docs/js/common.js` | `CATEGORIES` |
| `docs/graph.html` | `CAT_WL` |
| `docs/css/categories.css` | dark **同** light 兩套 |
| `docs/index.html` | `.cat-<slug> { --cat-color }` |

`tests/test_frontend.py::test_every_feed_category_is_wired_into_the_whole_frontend`
由 `RSS_FEEDS` 反查全部九個位，漏一個就紅。

⚠️ `docs/js/index.js` 個 `renderDailyBrief()` 入面仲有一條
`groups = ["新聞", "國際", "娛樂"]`——**嗰個係特登揀嘅三大類，唔係漏咗**，
所以個 test 冇檢查佢。

### 繁體轉換一律經 `src/hk_text.py`，唔好直接 call zhconv

`zhconv.convert(text, "zh-hk")`（同 `zh-hant`）一律將 **咸 → 鹹**——簡體個
「咸」一個字兼任「全部」（咸豐）同「鹹」（鹹魚）兩個意思。對真．簡體輸入
多數啱，對已經係繁體嘅文字就係破壞：碧咸（Beckham）→ 碧鹹。

所以拆咗做兩個入口，**按 caller 知唔知個輸入係咩語言嚟揀，唔靠估**：

| 入口 | 用喺邊 | 「咸」 |
|---|---|---|
| `from_simplified()` | `SIMPLIFIED_SOURCES`（cnBeta）嘅標題同內文 | 轉做「鹹」 |
| `to_hk()` | MiniMax 譯文、AI 抽嘅實體名、Google Trends 熱字 | 保護 |

`to_hk()` 只保護「咸」，其餘簡體漏網照掃（實測 M3 會出「湯告鲁斯」，
仍然會變「湯告魯斯」）——**呢個先係佢存在嘅原因，唔好為咗保護而閹咗佢**。

⚠️ 代價：真．簡體來源如果錯用 `to_hk()`，佢個「咸鱼」會停喺「咸魚」。
`tests/test_hk_text.py::test_no_module_calls_zhconv_directly_any_more`
守住冇人繞過。

⚠️ **譯 prompt 嗰句「唔肯定就保留英文原名」實測冇效**——M3 對啲名太有信心，
「唔肯定」條分支根本冇觸發（同一次測試 Scorsese 好咗、DiCaprio 差咗，係
noise）。留住係因為「用香港唔用台灣／大陸」本身有資訊量，但唔好當佢解決咗
人名譯法問題。真正修到嘅係上面個 `to_hk()`。

### 分類色彩系統（`docs/css/categories.css`）

```css
[data-cat="新聞"] { --cat-rgb: 232 124 124; --cat-active-bg: #3d1a1a; }
```

- `--cat-rgb`：用於 `rgb(var(--cat-rgb) / alpha)` 派生各種透明度顏色
- `--cat-active-bg`：filter button active 背景 / ai-pick card 背景
- 兩套：dark theme（`body` 預設）+ light theme（`body.theme-light`）
- 消費方式：CSS variable，不要 hardcode 顏色值
