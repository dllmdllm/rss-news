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
    # 娛樂
    {"name": "明報 娛樂",          "url": "https://news.mingpao.com/rss/ins/s00007.xml",                           "category": "娛樂"},
    # 消閒
    {"name": "明報 消閒",          "url": "https://news.mingpao.com/rss/ins/s00024.xml",                           "category": "消閒"},
    {"name": "WeekendHK",         "url": "https://www.weekendhk.com/feed",                                        "category": "消閒"},
    {"name": "GoTrip",            "url": "https://www.gotrip.hk/feed",                                            "category": "消閒"},
    # 科技
    {"name": "cnBeta",            "url": "https://rss.cnbeta.com.tw/",                                            "category": "科技"},
    {"name": "HKEPC",             "url": "https://www.hkepc.com/feed",                                            "category": "科技"},
    {"name": "Unwire",            "url": "https://unwire.hk/feed/",                                               "category": "科技"},
    {"name": "9to5Mac",           "url": "https://9to5mac.com/feed/",                                             "category": "科技"},
    {"name": "New MobileLife",    "url": "https://www.newmobilelife.com/feed/",                                   "category": "科技"},
    # 網媒
    {"name": "法庭線",             "url": "https://hkcourtnews.com/feed/",                                         "category": "網媒"},
    {"name": "The Collective HK", "url": "https://thecollectivehk.com/feed/",                                     "category": "網媒"},
    {"name": "The Witness",       "url": "https://thewitnesshk.com/feed/",                                        "category": "網媒"},
]

MAX_ITEMS_PER_FEED = 20
SCRAPE_CONCURRENCY = 15

# Sources that publish in Simplified Chinese — will be auto-converted to HK Traditional
SIMPLIFIED_SOURCES = {"cnBeta"}

# Sources that publish English titles/content. Titles are translated with MiniMax;
# article bodies remain in the original language for speed and reliability.
ENGLISH_SOURCES = {"9to5Mac", "The Collective HK"}
