# Shared across fetch.py and scrape.py — some sites (WeekendHK, GoTrip,
# Cloudflare-fronted feeds) reject non-browser user agents.
HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
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
    {"name": "WeekendHK",         "url": "https://www.weekendhk.com/feed",                                        "category": "消閒"},
    {"name": "GoTrip",            "url": "https://www.gotrip.hk/feed",                                            "category": "消閒"},
    # ❌ SkyPost 要聞 — 2026-07-25 移除。晴報轉型做「健康、娛樂、家庭生活資訊
    # 頻道」，唔再出港聞：/news/ 同首頁抽到嘅文全部係 健康/副刊 section，冇一篇
    # 係 fetcher 篩緊嘅 港聞，最新一篇仲要係兩星期前。佢個 sitemap 亦凍結咗喺
    # 2023 年（最大 article id 3614960，實際站上已去到 4165870），所以連
    # 「修好 sitemap」都救唔返。source 由 6 月起靜靜哋 0 篇（見下面
    # _fetch_skypost 嘅 error=None 問題）。_fetch_skypost 保留喺 fetch.py，
    # 萬一佢日後恢復港聞就 restore 呢一行。
    # 科技
    {"name": "cnBeta",            "url": "https://rss.cnbeta.com.tw/",                                            "category": "科技"},
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
    {"name": "法庭線",             "url": "https://hkcourtnews.com/feed/",                                         "category": "網媒"},
    {"name": "The Collective HK", "url": "https://thecollectivehk.com/feed/",                                     "category": "網媒"},
    {"name": "The Witness",       "url": "https://thewitnesshk.com/feed/",                                        "category": "網媒"},
    # HK01 — no RSS; uses public JSON feed API (web-data.api.hk01.com)
    {"name": "HK01 突發",          "url": "https://web-data.api.hk01.com/v2/feed/category/6",                      "category": "新聞", "fetcher": "hk01"},
    {"name": "HK01 社會",          "url": "https://web-data.api.hk01.com/v2/feed/category/2",                      "category": "新聞", "fetcher": "hk01"},
    {"name": "HK01 國際",          "url": "https://web-data.api.hk01.com/v2/feed/category/19",                     "category": "國際", "fetcher": "hk01"},
    {"name": "HK01 中國",          "url": "https://web-data.api.hk01.com/v2/feed/zone/5",                          "category": "國際", "fetcher": "hk01"},
    {"name": "HK01 娛樂",          "url": "https://web-data.api.hk01.com/v2/feed/zone/2",                          "category": "娛樂", "fetcher": "hk01"},
    {"name": "HK01 熱話",          "url": "https://web-data.api.hk01.com/v2/feed/zone/7",                          "category": "消閒", "fetcher": "hk01"},
    {"name": "HK01 深圳",          "url": "https://web-data.api.hk01.com/v2/feed/zone/25",                         "category": "消閒", "fetcher": "hk01"},
    # 電視台新聞
    {"name": "TVB 新聞",           "url": "https://news.tvb.com/sitemap.xml",                                      "category": "新聞", "fetcher": "tvb"},
    {"name": "Now 新聞",           "url": "https://newsapi1.now.com/pccw-news-api/api/getNewsListv2?category=119&pageNo=1&pageSize=30", "category": "新聞", "fetcher": "nowtv"},
    # 中國版（category=122）2026-07 起 API 回傳空，probe 過 121-130 都冇——121 係財經
    {"name": "Now 國際",           "url": "https://newsapi1.now.com/pccw-news-api/api/getNewsListv2?category=120&pageNo=1&pageSize=30", "category": "國際", "fetcher": "nowtv"},
]

MAX_ITEMS_PER_FEED = 20
SCRAPE_CONCURRENCY = 15

# Sources that publish in Simplified Chinese — will be auto-converted to HK Traditional
SIMPLIFIED_SOURCES = {"cnBeta"}

# Sources that publish English titles/content. Titles are translated with MiniMax;
# article bodies remain in the original language for speed and reliability.
ENGLISH_SOURCES = {"9to5Mac", "The Collective HK"}
