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
│   ├── daily_brief.py    # 每日早報：HKT 06:00 後首個 build 綜合 24h top stories（MiniMax）→ daily_brief.json + Telegram
│   ├── entity_digest.py  # 實體摘要：聚合人物 / 機構 → entities.json
│   ├── minimax_client.py # MiniMax API thin wrapper（共用 HTTP shape + thinking 參數）
│   └── feeds.py          # RSS 來源定義及常數
├── build.py              # 主程式：fetch → scrape → analyse → cluster → 輸出 JSON
├── docs/                 # GitHub Pages 根目錄
│   ├── index.html        # 文章列表頁（含 AI tab）
│   ├── article.html      # 文章閱讀頁
│   ├── js/
│   │   ├── index.js      # 列表頁邏輯（搜尋 / tab / AI 功能）
│   │   ├── article.js    # 文章閱讀邏輯（TTS / highlights）
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
│       ├── daily_brief.json    # 今日早報（index 頁早報卡 + TTS）
│       ├── feed_http_cache.json  # HTTP 304 cache
│       └── content/            # 各文章完整 HTML（{id}.json）
├── CLAUDE.md             # 本文件（同時作 AGENTS.md 使用）
├── requirements.txt
└── .github/
    └── workflows/
        └── update.yml    # GitHub Actions：每 20 分鐘執行 build.py（job timeout 25 min）
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
                          ├─ entity_digest.py   實體摘要
                          └─ save_json()  → docs/data/
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
cnBeta、HKEPC、Unwire、9to5Mac、New MobileLife、TVB News（新增）、Now News（新增）

### 網媒
法庭線、The Collective HK、香港法庭新聞、SkyPost（自訂解析器）

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
- **thinking 參數**：所有 structured-output 調用（translate / analyse / panel / entity）
  都傳 `thinking={"type": "disabled"}` — M3 預設開 thinking，reasoning tokens 會食
  `max_tokens` 令 JSON 答案被截斷；M2.7 接受呢個參數但無視佢（2026-06-10 probe 確認），
  所以傳咗都安全。遷移去 M3 時唔使再改

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

注意：`compute_embeddings`（sentence-transformers）係 sync code，必須經
`run_in_executor` + `wait_for` 跑——直接喺 event loop 上執行會令全局
`wait_for(850)` 無法觸發（loop 被 block），一 hang 就食晒成個 job timeout，
嗰一輪乜都 push 唔到。

### fetch per-feed 超時

`fetch_all()` 每個 feed 包一層 `asyncio.wait_for(75s)`。冇呢層嘅話，
單一 feed 卡死（SkyPost sitemap walk 最慢）會食晒 build.py 嘅 150s fetch
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
- `key_sentences` 用於文章閱讀頁高亮顯示

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

`src/panel_digest.py` / `src/entity_digest.py` / `src/breaking_alert.py` 各自有
module-level 絕對輸出路徑（唔跟 `build.DATA_DIR`）。任何跑 `build.main()` 嘅 test
必須 monkeypatch 晒呢啲路徑，否則 dry run 會改寫真數據（試過 wipe `panel_digests.json`）。

### 分類色彩系統（`docs/css/categories.css`）

```css
[data-cat="新聞"] { --cat-rgb: 232 124 124; --cat-active-bg: #3d1a1a; }
```

- `--cat-rgb`：用於 `rgb(var(--cat-rgb) / alpha)` 派生各種透明度顏色
- `--cat-active-bg`：filter button active 背景 / ai-pick card 背景
- 兩套：dark theme（`body` 預設）+ light theme（`body.theme-light`）
- 消費方式：CSS variable，不要 hardcode 顏色值
