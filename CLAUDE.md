# rss-news — Project Guide

## 專案目標

純靜態 RSS 新聞聚合器，部署於 GitHub Pages。
抓取各新聞源的**完整文章內容（全文＋圖片，保留原始順序）**，生成靜態 HTML 頁面。

---

## 架構

```
rss-news/
├── src/
│   ├── fetch.py          # 並發抓取 RSS feeds（asyncio + aiohttp）
│   ├── scrape.py         # 全文抓取，抽出正文＋圖片（trafilatura）
│   ├── analyse.py        # AI 分析：摘要 / 評分 / 標籤 / 情緒（MiniMax）
│   └── feeds.py          # RSS 來源定義及常數
├── build.py              # 主程式：fetch → scrape → analyse → cluster → 輸出 JSON
├── docs/                 # GitHub Pages 根目錄
│   ├── index.html        # 文章列表頁
│   ├── article.html      # 文章閱讀頁（模板）
│   └── data/
│       ├── articles.json # 所有文章數據（single source of truth）
│       └── analyses.json # AI 分析結果 cache（keyed by article id）
├── CLAUDE.md             # 本文件（同時作 AGENTS.md 使用）
├── requirements.txt
└── .github/
    └── workflows/
        └── update.yml    # GitHub Actions：每 20 分鐘執行 build.py
```

---

## 數據流

```
RSS Feed
  └─→ fetch.py      抓標題 / URL / 日期 / 來源 / thumbnail
        └─→ scrape.py   並發訪問原文，trafilatura 抽出正文＋圖片
              └─→ analyse.py  AI 分析（摘要 / score / tags / sentiment / topic）
                    └─→ build.py  cluster_articles() → 排序 → 寫入 articles.json
```

---

## 技術選型

| 用途 | 工具 | 版本 | 原因 |
|---|---|---|---|
| RSS 解析 | `feedparser` | 6.0.12 | 穩定，格式相容性好 |
| 並發抓取 | `asyncio` + `aiohttp` | 3.13.5 | 同時抓多篇，速度快 |
| 全文解析 | `trafilatura` | 2.0.0 | 自動識別正文、保留圖片順序、抗噪聲強 |
| HTML 解析 | `beautifulsoup4` | 4.14.3 | 自定義元素展開（星島 gallery）、圖片修復 |
| 繁簡轉換 | `zhconv` | 1.4.3 | 簡體 → 香港繁體 |
| 翻譯 | `deep-translator` | 1.11.4 | 英文來源自動翻譯 |
| 反爬蟲繞過 | `cloudscraper` | 1.2.71 | 繞過 Cloudflare 驗證 |
| 環境變數 | `python-dotenv` | 1.2.2 | 讀取 .env（API Key）|
| 數據格式 | JSON | — | 輕量，方便前端 search / AI 擴展 |
| 前端 | 純 HTML + Vanilla JS | — | 快，無框架開銷 |
| AI 分析 | MiniMax M2.7 | — | 摘要 / 評分 / 標籤 / 情緒 / 話題 |
| 執行環境 | Python | 3.13.13 | 最新穩定版 |

---

## RSS 來源

定義在 `src/fetch.py` 的 `RSS_FEEDS` 列表，格式：

```python
{"name": "來源名稱", "url": "RSS URL", "category": "分類"}
```

### 新聞（本地）

| 來源 | URL | 備註 |
|---|---|---|
| RTHK 本地 | https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_clocal.xml | RSS ✓ |
| 明報即時 | https://news.mingpao.com/rss/ins/s00001.xml | RSS ✓ |
| HK01 突發 | https://www.hk01.com/channel/6/ | ⚠️ 非 RSS，需爬蟲 |
| HK01 社會 | https://www.hk01.com/channel/2/ | ⚠️ 非 RSS，需爬蟲 |
| 東網 本地 | https://hk.on.cc/hk/news/index.html | ⚠️ 非 RSS，需爬蟲 |
| 星島頭條 | https://www.stheadline.com/rss | RSS ✓ |

### 國際

| 來源 | URL | 備註 |
|---|---|---|
| RTHK 國際 | https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_cinternational.xml | RSS ✓ |
| RTHK 大中華 | https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_greaterchina.xml | RSS ✓ |
| 明報 國際 | https://news.mingpao.com/rss/ins/s00005.xml | RSS ✓ |
| 明報 中國 | https://news.mingpao.com/rss/ins/s00004.xml | RSS ✓ |
| 東網 國際 | https://hk.on.cc/hk/intnews/index.html | ⚠️ 非 RSS，需爬蟲 |
| 星島 即時中國 | https://www.stheadline.com/realtime-china/ | ⚠️ 需確認是否有 RSS |
| 星島 即時國際 | https://www.stheadline.com/realtime-world/ | ⚠️ 需確認是否有 RSS |
| HK01 即時國際 | https://www.hk01.com/channel/19/ | ⚠️ 非 RSS，需爬蟲 |
| HK01 中國 | https://www.hk01.com/zone/5/ | ⚠️ 非 RSS，需爬蟲 |

### 娛樂

| 來源 | URL | 備註 |
|---|---|---|
| 明報 娛樂 | https://news.mingpao.com/rss/ins/s00007.xml | RSS ✓ |
| 東網 娛樂 | https://hk.on.cc/hk/entertainment/index.html | ⚠️ 非 RSS，需爬蟲 |
| 星島 娛樂 | https://www.stheadline.com/entertainment | ⚠️ 需確認是否有 RSS |
| HK01 娛樂 | https://www.hk01.com/zone/2/ | ⚠️ 非 RSS，需爬蟲 |

### 消閒

| 來源 | URL | 備註 |
|---|---|---|
| 明報 消閒 | https://news.mingpao.com/rss/ins/s00024.xml | RSS ✓ |
| WeekendHK | https://www.weekendhk.com/feed | RSS ✓ |
| GoTrip | https://www.gotrip.hk/feed | RSS ✓ |

### 科技

| 來源 | URL | 備註 |
|---|---|---|
| cnBeta | https://rss.cnbeta.com.tw/ | RSS ✓ |
| HKEPC | https://www.hkepc.com/feed | RSS ✓ |
| Unwire | https://unwire.hk/feed/ | RSS ✓ |
| 9to5Mac | https://9to5mac.com/feed/ | RSS ✓ |
| New MobileLife | https://www.newmobilelife.com/feed/ | RSS ✓ |

### 網媒

| 來源 | URL | 備註 |
|---|---|---|
| 法庭線 | https://hkcourtnews.com/feed/ | RSS ✓ |
| The Collective HK | https://thecollectivehk.com/feed/ | RSS ✓ |
| 香港法庭新聞 | https://thewitnesshk.com/ | ⚠️ 非 RSS，需爬蟲 |

---

## 開發階段

- **Phase 1（完成）** — 全文抓取 + 靜態頁面（列表頁 + 文章閱讀頁）
- **Phase 2（完成）** — AI 分析：摘要、重要性評分、標籤、情緒、話題 clustering
- **Phase 3（完成）** — Client-side 搜尋（Fuse.js，模糊匹配標題/摘要/標籤/來源/分類）

---

## 本地執行

```bash
pip install -r requirements.txt
cp .env.example .env        # 填入 MINIMAX_API_KEY
python build.py
# 輸出：docs/data/articles.json、docs/data/analyses.json
```

---

## 自動化

GitHub Actions（`.github/workflows/update.yml`）每 20 分鐘執行一次 `build.py`，
若 `docs/data/` 有變更則自動 commit & push。

Secret 名稱：`MINIMAX_API_KEY`（在 repo Settings → Secrets → Actions 設定）

---

## 注意事項

- 部分網站會封鎖爬蟲（需設定 User-Agent）
- `trafilatura` 對 JavaScript 渲染的頁面效果有限（暫不處理）
- 圖片使用原始 URL（不下載到本地），避免 repo 過大

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
- Response 格式係 Anthropic 標準：`data["content"][0]["text"]`
- 錯誤碼 `overloaded_error`（529）需 retry，加入 10s/20s backoff

### AI 分析並發數

`ANALYSE_CONCURRENCY = 10`（`src/analyse.py`）

MiniMax-M2.7 Token Plan 上限為 **500 RPM**，並發 10 實測安全。
若觸發 rate limit（錯誤碼 1002），可調低至 5。

### `_add_featured_image` 插入位置

`src/scrape.py` 中，縮圖必須插入 `<body>` **內部**，而不是字串最前：

```python
# 正確
content.replace('<body>', f'<body>{img}', 1)

# 錯誤（img 被 innerHTML 忽略）
img + content
```

原因：`BeautifulSoup(content, "html.parser")` 會將 HTML fragment 包成完整
`<html><head></head><body>…</body></html>` 結構。若將 `<img>` 插在字串最前，
它會在 `<html>` 標籤之前，瀏覽器的 `innerHTML` 賦值會忽略 `<html>` 以外的內容。

### GitHub Actions push 策略

`.github/workflows/update.yml` 每 20 分鐘觸發一次，commit `docs/data/` 後會最多重試 3 次：

```bash
git fetch origin main
git rebase origin/main -X ours
git push origin HEAD:main
```

原因：workflow 可能同 Codex 或上一個自動更新 commit 同時推送，會出現 push rejected。
每次 retry 都先重新 fetch/rebase，rebase 後 HEAD 嚴格 ahead of `origin/main`，
所以用 plain fast-forward push，**不需要** `--force` 或 `--force-with-lease`。
`-X ours` 只用於 rebase 衝突時保留本次新生成的 `docs/data/` 內容。

### 圖片 hotlink 保護

`docs/article.html` 所有 `<img>` 設有：

```javascript
img.referrerPolicy = "no-referrer";
```

部分新聞網站（明報、RTHK 等）會驗證 Referer header，設為 `no-referrer` 可繞過。
`<meta name="referrer" content="no-referrer">` 亦在 `<head>` 中設定作雙重保障。

### analyses.json cache 結構

```json
{
  "article_id_12char": {
    "summary": "・重點1\n・重點2",
    "score": 8,
    "tags": ["標籤A", "標籤B"],
    "sentiment": "negative",
    "topic": "標準化話題名稱"
  }
}
```

- 舊版 `summaries.json`（只有摘要字串）在首次執行時會自動 migrate 至此格式
- `score: null` 代表係 migrate 過來的舊 entry，下次 build 會重新分析
- `topic` 用於 `build.py` 的 `cluster_articles()`：相同 topic 的文章歸為一組
