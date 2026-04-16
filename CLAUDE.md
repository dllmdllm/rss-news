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
│   └── build.py          # 生成靜態 HTML 頁面
├── docs/                 # GitHub Pages 根目錄
│   ├── index.html        # 文章列表頁
│   ├── article.html      # 文章閱讀頁（模板）
│   └── data/
│       └── articles.json # 所有文章數據（單一 source of truth）
├── CLAUDE.md             # 本文件
├── requirements.txt
└── .github/
    └── workflows/
        └── update.yml    # GitHub Actions：定時執行 build.py
```

---

## 數據流

```
RSS Feed
  └─→ fetch.py       抓標題 / URL / 日期 / 來源
        └─→ scrape.py  並發訪問原文，trafilatura 抽出正文＋圖片（保順序）
              └─→ build.py  寫入 articles.json，生成 HTML 頁面
```

---

## 技術選型

| 用途 | 工具 | 原因 |
|---|---|---|
| RSS 解析 | `feedparser` | 穩定，格式相容性好 |
| 並發抓取 | `asyncio` + `aiohttp` | 同時抓多篇，速度快 |
| 全文解析 | `trafilatura` | 自動識別正文、保留圖片順序、抗噪聲強 |
| 數據格式 | JSON | 輕量，方便前端 search / AI 擴展 |
| 前端 | 純 HTML + Vanilla JS | 快，無框架開銷 |

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

- **Phase 1（當前）** — 全文抓取 + 靜態頁面（列表頁 + 文章閱讀頁）
- **Phase 2** — Client-side 搜尋（Fuse.js，無需後端）
- **Phase 3** — Claude API 整合（文章摘要、重要性評分）

---

## 本地執行

```bash
pip install -r requirements.txt
python src/build.py
# 輸出：docs/index.html、docs/data/articles.json
```

---

## 自動化

GitHub Actions（`.github/workflows/update.yml`）每 10 分鐘執行一次 `build.py`，
若 `docs/` 有變更則自動 commit & push。

---

## 注意事項

- 部分網站會封鎖爬蟲（需設定 User-Agent）
- `trafilatura` 對 JavaScript 渲染的頁面效果有限（暫不處理）
- 圖片使用原始 URL（不下載到本地），避免 repo 過大
