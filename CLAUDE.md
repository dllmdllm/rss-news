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
├── AGENTS.md             # ⚠️ CLAUDE.md 嘅鏡像，改完 CLAUDE.md 要 copy 過去
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

### 已移除來源（2026-07-25）
兩個都靜靜哋 0 篇超過一個月先發現，成因見下面「靜默 0 篇」一節：

- **Engadget 中文**：`chinese.engadget.com` 連 DNS 都解析唔到，網站已停運。
  Yahoo 香港吸收咗佢嘅中文科技內容（Engadget 中文版本身就係 Yahoo 旗下），
  所以加咗 `Yahoo 科技` 頂上。⚠️ Yahoo 個 `/rss` 係空殼（767 bytes、0 個
  `<item>`，2026-07-25 實測），一定要行 HTML fetcher；listing 頁亦冇日期，
  要逐篇開文攞 `<time datetime>`（listing→逐篇嘅 N+1 pattern）。
  全文交返 trafilatura 就得，唔使 custom parser（實測 2500-2900 字、9 張圖）
- **SkyPost 要聞**：晴報轉型做「健康、娛樂、家庭生活資訊頻道」，唔再出港聞
  ——`/news/` 同首頁抽到嘅文全部係 健康/副刊 section，冇一篇係 `港聞`。佢個
  sitemap 亦凍結咗喺 2023 年（最大 article id 3614960，實際站上已去到
  4165870），所以連「修好 sitemap」都救唔返——唔係 parser 壞，係個 source 冇咗
  新聞。**相關 code 已全部刪走**（`fetch.py` 嘅 `_fetch_skypost` 同 `_skypost_*`
  helper、`scrape.py` 嘅 `_build_skypost_content` / `_is_skypost_url` /
  hket inline-image regex、2 個 test），要翻查就睇 git history

---

## 開發階段

- **Phase 1（完成）** — 全文抓取 + 靜態頁面（列表頁 + 文章閱讀頁）
- **Phase 2（完成）** — AI 分析：摘要、重要性評分、標籤、情緒、話題 clustering
- **Phase 3（完成）** — Client-side 搜尋（Fuse.js，模糊匹配）
- **Phase 4（完成）** — AI tab：情緒概覽、話題聚焦、事件篩選、熱門標籤、今日重點
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
- **Skill：`skills/project-conventions/SKILL.md`** — 抽出下方「設計決定」內容，當 Claude 改 `src/analyse.py`、`src/scrape.py`、`src/fetch.py`、`build.py`、`docs/article.html`、`.github/workflows/update.yml` 時會自動載入
- **Skill：`skills/add-feed-source/SKILL.md`**（user-invocable `/add-feed-source`）— 加新 RSS / 新聞源嘅 workflow：分類、parser 選型、feeds.py entry、smoke test、CLAUDE.md 同步
- **Skill：`skills/diagnose-feed/SKILL.md`**（user-invocable `/diagnose-feed`）— 單一 feed triage：跑 `diagnose.py <name-pattern>`，stage 化打印 fetch → scrape → preview（可選 `--analyse`），唔使等 15 min GH Actions cycle 就 pinpoint 邊個 stage 壞咗
- **Skill：`skills/release-frontend/SKILL.md`**（user-invocable `/release-frontend`）— 前端 release checklist：verify SW cache bump → `pytest tests/test_frontend.py` → vendor `?v=` token → conventional commit → push（注意 `update.yml` push event path filter 唔包 `docs/**`，前端 push 唔會自動 trigger workflow，要等 20 min cron 或手動 dispatch）
- **Subagent：`agents/async-timeout-reviewer.md`** — review 任何 timeout 改動（`asyncio.wait_for` / cloudscraper fallback / GH Actions `timeout-minutes`），確保三層 nested timeout 唔會錯位
- **Subagent：`agents/feed-parser-reviewer.md`** — review `src/scrape.py` 嘅 per-site parser 改動（`_build_hk01_content` / `_build_tvb_content` / `_NOWSNEWS_JUNK_RE` / `_add_featured_image` / cloudscraper fallback），catch silent regression（parser 返空 → fallback trafilatura → SEO preview only）
- **Global Skill：`andrej-karpathy-skills:karpathy-guidelines`**（user-scope plugin，唔喺本 repo 入面）— 寫 / review / refactor code 時應該套用，避免 overcomplication、做 surgical change、surface assumptions、define verifiable success criteria
- **MCP server：`context7`** — Library doc lookup（fetch 即時 React / aiohttp / trafilatura / feedparser docs，避免 hallucinate API）
- **MCP server：`github`** — GitHub repo / Actions / PR 操作（list workflow run、download log、re-dispatch、status check）
- **`settings.local.json`** — 個人 permission allowlist + hook 設定（PreToolUse + PostToolUse）

⚠️ Skill 內容係下方「設計決定」嘅 copy，**改其中一邊記得同步另一邊**（或之後重構成一邊 reference 另一邊）。

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

Yahoo 新聞喺文章下面有個「其他人也在看」欄，**嵌住成篇完整推薦文章**（唔止
連結）。所以 trafilatura 會一鑊過將完全無關嘅新聞掃埋入正文——實際見過一篇
Claude Opus 5 嘅科技文夾住自助餐優惠同 LeBron James 轉會（2026-07-25 用戶
報告，正正係加咗呢個 source 之後）。呢啲垃圾會餵落 `analyse.py`，摘要／標籤／
topic 全部污染。

解法係鎖死最窄嗰個純內文容器：

```python
_YAHOO_BODY_SELECTOR = "section.module-article-body div.atoms"
```

`div.atoms` 之外嘅嘢（麵包屑、重複標題、byline、出版商 logo、推薦欄）全部係
chrome。另外 `div.atoms` 入面仲會夾住「廣告」字樣嘅 spacer 段落，要用
`_YAHOO_AD_MARKER_RE` 濾走。

⚠️ **教訓**：當初驗證只量咗字數（2,500-2,900 字）就當合格——但垃圾正正係
**撐大**字數嗰樣嘢，所以個數字睇落好健康。驗 parser 一定要真係讀返抽到嘅
文字，唔可以淨係睇長度。清乾淨之後每篇得 500-1,800 字（Yahoo 科技多數係短快訊）。

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

兩條獨立 Telegram 提示通道，共用同一份關鍵字清單：

- **慢速通道**（`keyword_alert.py`）：隨主 build（~20 分鐘一輪，self-hosted），
  比對全部 source 嘅 title/summary/tags，去重靠 `docs/data/keyword_alerts.json`。
  ⚠️ **2026-07-22 起停用**——同快速通道職能重疊，兩條一齊開太密集
  （用戶反映）。`build.py` 頂部 `SLOW_KEYWORD_ALERTS_ENABLED = False`
  常數控制，`main()` 淨係唔 call `send_keyword_alerts()`；module 本身
  同 test 保持齊全冇刪，想返都係一行 flip
- **快速通道**（`fast_watch.py`）：獨立 `fast-watch.yml`（ubuntu，5 分鐘一輪，
  00:00-07:00 HKT 除外），淨查星島頭條/am730/TVB新聞三個最快 source 嘅標題
  （唔 scrape 全文、唔叫 AI），去重靠 GitHub Actions cache（跟 `guardian.yml`
  pattern）。**刻意唔碰 `docs/data`、唔 git push**——避免同主 build 嘅 push 撞。
  慢速通道停用之後，呢條係現時**唯一**仲活躍嘅 keyword alert 通道

兩條通道格式故意唔同（⚡ 快訊 vs 🔔 提醒），因為兩邊冇共用 dedup 狀態，
偶爾會見到同一篇文兩邊各推一次——呢個已知取捨喺慢速通道停用之後其實
唔再出現（淨返一條通道跑緊）。

**Trending 字標籤（2026-07-22）**：Match 到嘅 keyword 若嚟自 Google Trends
（`_is_trending_keyword()` / `_trending_keyword` flag——喺 curated
`WATCH_KEYWORDS` 揾唔到）,訊息嘅 keyword 後面會加 ` (From Google Trend)`，
等用戶一眼分到係人手揀嘅字定係 Google 熱搜自動撞中。兩條通道（`fast_watch.py`
嘅 `_format_text()` / `keyword_alert.py` 嘅 `_format_alert_text()`）都套用。

**關鍵字清單來源**：`WATCH_KEYWORDS` 唔再係 hardcode 喺 code——用戶喺 Obsidian
vault（`RSS News - Watch Keywords.md`）隨時改，`build.main()` 開頭
`sync_watch_keywords_from_vault()`（self-hosted 先有 vault access）讀 vault、
寫落 repo-tracked `config/watch_keywords.txt`，兩條通道都讀呢個檔（快速通道
喺 ubuntu 見唔到 vault，靠 git checkout 攞到最新版，所以 `config/watch_keywords.txt`
一定要喺 `update.yml` 嘅 `stage_outputs()` 入面 add）。

⚠️ Vault note 一定要有 `## 關鍵字清單` 呢個標題，sync 先識分「上面自由寫嘅
說明文字」同「下面真正嘅關鍵字」——搵唔到標題就當成 invalid input 整個拒絕
同步（保留舊 config），**唔會**將說明文字當成關鍵字寫落去。2026-07-20
第一版冇呢層保護，`tests/test_build.py` 嘅 dry-run 冧咗真 `config/watch_keywords.txt`
（vault 說明文字全部變咗「關鍵字」），先加返呢個 fail-closed 設計 + regression test。

字面 substring match，唔分大小寫，OR 邏輯——淨係 "OpenAI" 唔會 match 到
"ChatGPT"，要中英對照/品牌/人名全部列晒。

**`context:` directive（2026-07-21）**：「死亡」「交通意外」呢類字面太闊嘅
keyword，vault 入面可以喺一段關鍵字前面加一行 `context: 港人, 本港, 本地, 香港`，
之後每個關鍵字都要「連同呢組字眼之一一齊出現」先算 match；落返一行淨係
`context:`（冇內容）就解除限制。Parse 邏輯喺 `keyword_alert._parse_keyword_rules()`，
產出 `WATCH_KEYWORDS` + `KEYWORD_CONTEXT`（dict），兩條通道嘅 match function
（`_first_qualifying_keyword()` / `fast_watch._match_keyword()`）都食呢個 dict。
而家套咗喺「交通意外/車禍/撞車/相撞」「死亡/斃命/倒斃/浮屍」「自殺/墮樓/跳樓」
「暴雨警告/黑雨/紅雨/黃雨」（暴雨組 2026-07-21 補加——「紅雨」「黑雨」成日
俾人喺同天氣完全無關嘅職場/生活八卦文度借用，例如「紅雨可WFH」。⚠️ 呢個
唔係 100% 有效：如果篇八卦文本身都提到「港人」呢類字（同天氣無關嘅另一句），
都會照樣漏網——已知取捨，唔係 bug）。

**同一單新聞去重**：兩條通道各自實現，冇共用 state——慢速通道用 build.py
已計好嘅 AI `cluster_id`（`detect_keyword_matches()` 同 cluster 淨揀最新一篇，
記喺 `keyword_alerts.json` 嘅 `alerted_clusters`）；快速通道冇 clustering 可用，
改用「同一個 keyword 30 分鐘內唔再送」嘅 cooldown（`fast_watch.py` 嘅
`KEYWORD_COOLDOWN_MINUTES`，state 存喺 `cooldown` key）。Cooldown 一定要逐篇
check + send 完即時更新，唔可以喺 loop 之前一次過計 eligible list——試過
因為咁樣，同一個 run 入面 3 篇撞正同一 keyword 嘅文一齊送晒（2026-07-21）。

**Keyword-level cooldown（2026-07-21，慢速通道補加）**：用戶反映「六合彩」
「陳嘉信」呢類持續事件跨越幾個唔同 `cluster_id`（AI 分到唔同角度＝唔同
topic）都會分別觸發 alert，`alerted_clusters` 嘅 cluster dedup 唔夠。
`keyword_alert.py` 而家都有 `KEYWORD_COOLDOWN_MINUTES`（30 分鐘），同
`fast_watch.py` 同一套設計：`detect_keyword_matches()` 用 build 開始嗰陣嘅
cooldown snapshot 預篩一次，`send_keyword_alerts()` 送嗰陣再逐篇 check +
即時更新（避免同一個 run 入面唔同 cluster 撞正同一 keyword 齊齊送晒）。
State 存喺 `keyword_alerts.json` 新增嘅 `cooldown` key。

**Google Trends 自動並入監控（`src/trends_watch.py`，2026-07-21）**：用戶要求
Google 香港熱門搜尋自動加入監控清單，match 到就當普通 keyword alert 送
（唔開獨立 channel、唔額外標記）。Google 冇官方「過去一小時」granularity 嘅
trending API，用官方支援嘅 daily trending RSS feed（`trends.google.com/trending/rss?geo=HK`，
免 auth，唔算 unofficial scraping）代替——但實測（2026-07-21）item 嘅
pubDate 相隔 10-30 分鐘，個榜好快轉勻，「daily」淨係個 feed 個名，唔係
實際更新頻率。`sync_trending_keywords()` 跟返 `build.py` 主 pipeline 同頻率
（~20 分鐘一次 build 都真.去 fetch，冇 gate），由 `build.py` 喺 vault sync
之後、fetch 之前 call（20s cap，失敗/逾時就保留舊清單）。每次成功都完全
覆寫（唔似 `WATCH_KEYWORDS` 咁累積），避免舊嘅 trending 詞賴喺個清單度；
`config/trending_keywords.txt` 第一行 `# synced <ISO timestamp>` 純粹記錄
「上次成功 fetch 幾時」，冇 gating 邏輯食呢個值。單字（例如「金」）撞正
太多常見詞、substring match 誤鳴風險太高，`MIN_KEYWORD_LEN=2` 過濾走；
標題經 `zhconv` 轉做香港繁體（Google 有時返簡體，唔轉嘅話成隻詞永遠
match 唔中網站嘅繁體內文）。兩條通道都用 `load_trending_keywords()` 讀
呢個 config，同 `WATCH_KEYWORDS` 合併（`dict.fromkeys()` 去重）先做
match——冇喺 `KEYWORD_CONTEXT` 登記，所以 trending 字自動當冇 context 限制。

**Trending 字獨立節流（2026-07-21）**：Trending 字冇 curated `WATCH_KEYWORDS`
咁「特登揀嘅」，成日對應緊持續幾個鐘嘅日常熱話（六合彩攪珠、八卦人物）——
兩條通道都畀佢哋更長嘅 cooldown（`TRENDING_COOLDOWN_MINUTES=90`，curated
keyword 用返 `KEYWORD_COOLDOWN_MINUTES=30`）+ 獨立細 quota
（`MAX_TRENDING_ALERTS_PER_BUILD=2` / `MAX_TRENDING_ALERTS_PER_RUN=1`），
避免佢哋洗晒成個 `MAX_ALERTS_PER_BUILD`/`MAX_ALERTS_PER_RUN`、擠走真正
人手揀嘅 keyword。判斷「呢個 keyword 係咪 trending」：喺 trending 清單
但唔喺 `WATCH_KEYWORDS`（兩邊都有嘅字當 curated，`WATCH_KEYWORDS` 贏）。
探測過用 `ht:approx_traffic`（feed 附帶嘅搜尋量估算）做過濾——實測發現
高流量同「值得 alert」冇關係（六合彩呢類例行內容流量反而最高），冇採用。

⚠️ **Test isolation 陷阱**（同 2026-07-21 `KEYWORD_CONTEXT` 嗰個一樣，重複出現
過兩次）：一旦 self-hosted 機真.跑過 `sync_trending_keywords()`，
`config/trending_keywords.txt` 會有真實內容並提交入 repo，之後任何冇
monkeypatch `load_trending_keywords()`（`keyword_alert.py`）/
`TRENDING_KEYWORDS`（`fast_watch.py`，module-level，喺 import 讀一次）嘅
test，都會意外撞中真實熱門字。兩個 test file 已加 autouse fixture 預設清空，
新 test 唔使再手動處理。

### 靜默 0 篇：來源斷更偵測（`src/source_health.py`）

**呢個坑中過三次**：東網娛樂（2026-07-15）、SkyPost + Engadget（2026-07-25，
兩個都死咗成個月先發現）。共同模式係 fetcher 攞到 0 篇但 `error=None`
（當時 `_fetch_skypost` 最尾無條件 `return articles, None, False`——呢個
fetcher 已隨 source 一齊刪走，但同一個形狀喺其他 fetcher 仍然存在），
於是 build 全綠、每個 stage 都 ok、`articles.json` 淨係靜靜哋寫住
`effective_count: 0`，冇任何嘢會嘈。

`check_source_health()` 喺 `build.py` 每次 build 尾段跑，將每個 source 嘅
`effective_count` 摺入 `docs/data/source_health.json`：

- 一次 0 篇唔算數（上游斷線／深夜冇新聞／HTTP 304 都會 0）——要**連續
  `ZERO_ALERT_AFTER_HOURS`（24 小時）**都係 0 先當斷更
- 告警形狀跟 `guardian.yml`：健康→死嗰下嗌一次 🟠，恢復發 🟢，
  持續死就每 `REMIND_EVERY_HOURS`（24 小時）先提一次，唔會每 20 分鐘洗版
- `evaluate_sources()` 係純函數（收 `now` / `state` 參數，冇 I/O），
  所以 test 可以直接餵假時鐘行完成個生命週期

⚠️ `docs/data/source_health.json` **一定要喺 `update.yml` 嘅 `stage_outputs()`
入面 add**——唔 stage 嘅話下次 checkout 清走佢，「連續幾耐 0 篇」永遠由零開始，
24 小時門檻夠唔到，成個偵測機制等於冇。（同 `daily_brief.json` /
`translated_content.json` 中過嘅伏一模一樣。）
Test 亦要 monkeypatch `STATE_PATH` 同 `TELEGRAM_BOT_TOKEN`，見「測試唔可以寫真
docs/data/」一節。

### GitHub cron 唔可靠：兩個 workflow 都要本機 dispatch 補位

GitHub 原生 cron 對高頻排程 throttle 得好犀利。2026-07-25 實測
`fast-watch.yml`（cron 寫住 `*/5`）**實際 run 之間相隔 80–480 分鐘**，
即係「5 分鐘 tripwire」真身係 1.5–8 小時一次。慢速通道 2026-07-22 停用之後
佢係唯一嘅 keyword 通道，所以等於 keyword alert 靜靜哋失效。

兩個 workflow 而家都由本機 Task Scheduler 推：

| Task | 頻率 | Script | 推邊個 workflow |
|---|---|---|---|
| `rss-news-dispatch` | 20 分鐘 | `C:\actions-runner\dispatch-update.ps1` | `update.yml` |
| `rss-news-fast-dispatch` | 5 分鐘 | `C:\actions-runner\dispatch-fast-watch.ps1` | `fast-watch.yml` |

兩個 script 都有同一個 race guard：見到有 run `queued`/`in_progress` 就唔再
dispatch（2026-06-09 撞過——第二個 dispatch 會 cancel 緊行緊嗰個）。

⚠️ `fast_watch.py` **本身冇 quiet-hours 檢查**，00:00-07:00 HKT 唔嘈全靠
cron 個 `23,0-15 UTC` 時段擋住。所以 `dispatch-fast-watch.ps1` 自己重複咗
一次呢個時段判斷——手動 dispatch 或者改個 script 嘅時候記住呢層，
唔係會半夜彈 Telegram。

### 分類色彩系統（`docs/css/categories.css`）

```css
[data-cat="新聞"] { --cat-rgb: 232 124 124; --cat-active-bg: #3d1a1a; }
```

- `--cat-rgb`：用於 `rgb(var(--cat-rgb) / alpha)` 派生各種透明度顏色
- `--cat-active-bg`：filter button active 背景 / ai-pick card 背景
- 兩套：dark theme（`body` 預設）+ light theme（`body.theme-light`）
- 消費方式：CSS variable，不要 hardcode 顏色值
