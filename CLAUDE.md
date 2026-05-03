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
│   ├── entity_digest.py  # 實體摘要：聚合人物 / 機構 → entities.json
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
│       ├── feed_http_cache.json  # HTTP 304 cache
│       └── content/            # 各文章完整 HTML（{id}.json）
├── CLAUDE.md             # 本文件（同時作 AGENTS.md 使用）
├── requirements.txt
└── .github/
    └── workflows/
        └── update.yml    # GitHub Actions：每 20 分鐘執行 build.py（timeout 15 min）
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
- **job timeout：15 分鐘**（防止卡住無限等待）
- 若 `docs/data/` 有變更則自動 commit & push（最多 retry 3 次 fetch/rebase/push）

Secrets：`MINIMAX_API_KEY`、`TELEGRAM_BOT_TOKEN`

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
- Response 格式：`data["content"][0]["text"]`
- 錯誤碼 `overloaded_error`（529）需 retry，10s/20s backoff
- Rate limit 1002 → 調低 `ANALYSE_CONCURRENCY`（目前 10，安全上限約 500 RPM）

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
asyncio.run(asyncio.wait_for(main(), timeout=780))  # 13 分鐘
```

配合 workflow `timeout-minutes: 15`，確保 job 不會無限運行。

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

### 分類色彩系統（`docs/css/categories.css`）

```css
[data-cat="新聞"] { --cat-rgb: 232 124 124; --cat-active-bg: #3d1a1a; }
```

- `--cat-rgb`：用於 `rgb(var(--cat-rgb) / alpha)` 派生各種透明度顏色
- `--cat-active-bg`：filter button active 背景 / ai-pick card 背景
- 兩套：dark theme（`body` 預設）+ light theme（`body.theme-light`）
- 消費方式：CSS variable，不要 hardcode 顏色值
