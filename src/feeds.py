# Shared across fetch.py and scrape.py — some sites (WeekendHK, GoTrip,
# Cloudflare-fronted feeds) reject non-browser user agents.
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}

# Explicit RSS identity for publishers rejecting the legacy browser header.
# One request only: an access denial remains an error, with no client rotation.
RSS_CLIENT_HEADERS = {
    "User-Agent": "rss-news/1.0 (+https://github.com/dllmdllm/rss-news)",
    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
}

RSS_FEEDS = [
    # 新聞
    {"name": "RTHK 本地",         "url": "https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_clocal.xml",        "category": "新聞"},
    {"name": "明報 本地",          "url": "https://news.mingpao.com/rss/ins/s00001.xml",                           "category": "新聞"},
    {"name": "am730",              "url": "https://www.am730.com.hk/sitemap.xml",                                  "category": "新聞", "fetcher": "am730",
     "max_items": 40,
     "url_category": {
         "/國際/": "國際", "/中國/": "國際",
         "/娛樂/": "娛樂",
         "/生活/": "消閒", "/健康/": "消閒", "/體育/": "消閒",
         "/科技/": "科技",
     }},
    {"name": "東網 本地",          "url": "https://hk.on.cc/hk/news/index.html",                                    "category": "新聞", "fetcher": "oncc", "oncc_section": "news"},
    {"name": "星島頭條",           "url": "https://www.stheadline.com/rss",                                        "category": "新聞",
     "max_items": 100,
     "url_category": {
         "/film-drama/": "娛樂", "/entertainment/": "娛樂",
         "/realtime-world/": "國際", "/realtime-china/": "國際",
         "/lifestyle/": "消閒", "/life/": "消閒",
         "/food/": "消閒", "/food-safety/": "消閒", "/travel/": "消閒",
         "/culture/": "消閒", "/parenting/": "消閒", "/health-care/": "消閒",
     }},
    # 國際
    {"name": "RTHK 國際",         "url": "https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_cinternational.xml", "category": "國際"},
    {"name": "RTHK 大中華",       "url": "https://rthk9.rthk.hk/rthk/news/rss/c_expressnews_greaterchina.xml",   "category": "國際"},
    {"name": "明報 國際",          "url": "https://news.mingpao.com/rss/ins/s00005.xml",                           "category": "國際"},
    {"name": "明報 中國",          "url": "https://news.mingpao.com/rss/ins/s00004.xml",                           "category": "國際"},
    {"name": "東網 國際",          "url": "https://hk.on.cc/hk/intnews/index.html",                                 "category": "國際", "fetcher": "oncc", "oncc_section": "intnews"},
    # 娛樂
    {"name": "明報 娛樂",          "url": "https://news.mingpao.com/rss/ins/s00007.xml",                           "category": "娛樂"},
    # 娛樂 index 頁係 client-side render（空殼），要用 on.cc 自己嘅
    # dailyList JSON feed（fetch.py _fetch_oncc_daily）
    {"name": "東網 娛樂",          "url": "https://hk.on.cc/hk/bkn/js/{date}/entertainment_dailyList.js",           "category": "娛樂", "fetcher": "oncc_daily", "oncc_section": "entertainment"},
    # 消閒
    {"name": "明報 消閒",          "url": "https://news.mingpao.com/rss/ins/s00024.xml",                           "category": "消閒"},
    {"name": "WeekendHK",         "url": "https://www.weekendhk.com/feed",                                        "category": "消閒", "request_timeout": 30},
    {"name": "GoTrip",            "url": "https://www.gotrip.hk/feed",                                            "category": "消閒"},
    # ❌ SkyPost 要聞 — 2026-07-25 移除，連 fetcher / scraper / test 一併刪走。
    # 晴報轉型做「健康、娛樂、家庭生活資訊頻道」，唔再出港聞：/news/ 同首頁抽到
    # 嘅文全部係 健康/副刊 section，冇一篇係 港聞。佢個 sitemap 亦凍結咗喺 2023
    # 年（最大 article id 3614960，實際站上已去到 4165870），所以連「修好
    # sitemap」都救唔返 —— 唔係 parser 壞，係個 source 冇咗新聞。
    # 想翻查舊 code（_fetch_skypost / _build_skypost_content）就睇 git history。
    # 科技
    # max_items is the legacy paid-enrichment budget, not collection eligibility.
    # The bounded provided RSS response is scanned for every in-window article.
    {"name": "cnBeta",            "url": "https://rss.cnbeta.com.tw/",                                            "category": "科技", "max_items": 45},
    {"name": "HKEPC",             "url": "https://www.hkepc.com/feed",                                            "category": "科技"},
    {"name": "Unwire",            "url": "https://unwire.hk/feed/",                                               "category": "科技"},
    # Engadget 中文（chinese.engadget.com）2026-07-25 移除：個 domain 連 DNS
    # 都解析唔到，網站已停運。Yahoo 香港吸收咗佢嘅中文科技內容（Engadget 中文版
    # 本身就係 Yahoo 旗下），所以用 Yahoo 科技頂上——但注意佢個 /rss 係空殼
    # （767 bytes、0 個 item），要行 HTML fetcher。
    {"name": "Yahoo 科技",         "url": "https://hk.news.yahoo.com/tech/",                                       "category": "科技", "fetcher": "yahoo"},
    {"name": "9to5Mac",           "url": "https://9to5mac.com/feed/",                                             "category": "科技"},
    {"name": "New MobileLife",    "url": "https://www.newmobilelife.com/feed/",                                   "category": "科技"},
    # 網媒
    # Both publishers advertise public WordPress REST discovery on their
    # homepage. Use its view-context posts index rather than unstable XML
    # responses on the Windows runner. Keep official RSS links for provenance.
    {"name": "法庭線",             "url": "https://hkcourtnews.com/feed/", "category": "網媒", "headers": RSS_CLIENT_HEADERS, "rss_media": True,
     "fetcher": "wordpress_public", "api_url": "https://hkcourtnews.com/wp-json/wp/v2/posts"},
    {"name": "The Collective HK", "url": "https://thecollectivehk.com/feed/", "category": "網媒", "headers": RSS_CLIENT_HEADERS, "rss_media": True,
     "fetcher": "wordpress_public", "api_url": "https://thecollectivehk.com/wp-json/wp/v2/posts"},
    {"name": "The Witness",       "url": "https://thewitnesshk.com/feed/",                                        "category": "網媒"},
    # HK01 — no RSS; uses public JSON feed API (web-data.api.hk01.com)
    {"name": "HK01 突發",          "url": "https://web-data.api.hk01.com/v2/feed/category/6",                      "category": "新聞", "fetcher": "hk01"},
    {"name": "HK01 社會",          "url": "https://web-data.api.hk01.com/v2/feed/category/2",                      "category": "新聞", "fetcher": "hk01"},
    {"name": "HK01 國際",          "url": "https://web-data.api.hk01.com/v2/feed/category/19",                     "category": "國際", "fetcher": "hk01"},
    {"name": "HK01 中國",          "url": "https://web-data.api.hk01.com/v2/feed/zone/5",                          "category": "國際", "fetcher": "hk01"},
    {"name": "HK01 娛樂",          "url": "https://web-data.api.hk01.com/v2/feed/zone/2",                          "category": "娛樂", "fetcher": "hk01"},
    {"name": "HK01 熱話",          "url": "https://web-data.api.hk01.com/v2/feed/zone/7",                          "category": "消閒", "fetcher": "hk01"},
    {"name": "HK01 深圳",          "url": "https://web-data.api.hk01.com/v2/feed/zone/25",                         "category": "消閒", "fetcher": "hk01"},
    # 外媒 — 外國媒體原文（英文，標題同全文都會經 MiniMax 譯做香港繁體）。
    # 同「國際」分開：國際係港媒寫嘅國際新聞，外媒係外國媒體自己嘅報道。
    # ⚠️ HuffPost 個 /section/front-page/feed 同 /section/style/feed 都係
    # HTTP 200 但 0 個 <item>（2026-07-30 實測）——揀 section 一定要真係數
    # entries，唔可以見到 200 就當得。呢種空殼正正係「靜默 0 篇」嘅來源。
    # Keep the previous paid-enrichment budgets as collection expands. Additional
    # in-window metadata is retained with honest pending enrichment status.
    {"name": "HuffPost 新聞",      "url": "https://www.huffpost.com/section/us-news/feed",                        "category": "外媒", "max_items": 15, "headers": RSS_CLIENT_HEADERS},
    {"name": "HuffPost 娛樂",      "url": "https://www.huffpost.com/section/entertainment/feed",                  "category": "外媒", "max_items": 15, "headers": RSS_CLIENT_HEADERS},
    {"name": "HuffPost 生活",      "url": "https://www.huffpost.com/section/life/feed",                           "category": "外媒", "max_items": 8, "headers": RSS_CLIENT_HEADERS, "rss_media": True},
    # 電視台新聞
    # Preserve the prior paid-enrichment allowance; the whole bounded sitemap
    # now supplies metadata instead of treating30 as an article eligibility cap.
    {"name": "TVB 新聞",           "url": "https://news.tvb.com/sitemap.xml",                                      "category": "新聞", "fetcher": "tvb", "max_items": 30},
    {"name": "Now 新聞",           "url": "https://newsapi1.now.com/pccw-news-api/api/getNewsListv2?category=119&pageNo=1&pageSize=30", "category": "新聞", "fetcher": "nowtv"},
    # 中國版（category=122）2026-07 起 API 回傳空，probe 過 121-130 都冇——121 係財經
    {"name": "Now 國際",           "url": "https://newsapi1.now.com/pccw-news-api/api/getNewsListv2?category=120&pageNo=1&pageSize=30", "category": "國際", "fetcher": "nowtv"},
]

# Legacy per-source paid-enrichment allowance and public API page sizing only.
# Collection uses the30h internal time window (the UI exposes24h) and explicit
# response/request/time budgets. It never silently drops eligible metadata at30.
MAX_ITEMS_PER_FEED = 30
SCRAPE_CONCURRENCY = 15

# Sources that publish in Simplified Chinese — will be auto-converted to HK Traditional
SIMPLIFIED_SOURCES = {"cnBeta"}

# Sources that publish English titles/content. Titles are translated with MiniMax;
# article bodies remain in the original language for speed and reliability.
ENGLISH_SOURCES = {
    "9to5Mac",
    "The Collective HK",
    "HuffPost 新聞",
    "HuffPost 娛樂",
    "HuffPost 生活",
}
