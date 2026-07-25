# rss-news — 設計決定嘅案發經過

`CLAUDE.md` 放**規則**（每個 session 都載入，所以要短）。呢度放**點解會有呢條規則**
——實際撞過嘅 bug、試過唔得嘅方向、量度返嚟嘅數字。

規則本身唔喺呢度。要改 code 嘅話讀 `CLAUDE.md` 就夠；**淨係當你想推翻某條規則、
或者想知「係咪真係要咁做」嗰陣先讀呢度**。每節嘅 anchor 由 CLAUDE.md 連過嚟。

> 想睇逐日 changelog（新功能、UI 改動、運維事故）：Obsidian vault
> `10_Projects/Active/RSS_News/`。呢個檔淨係記「解釋緊 CLAUDE.md 某條規則」嗰啲。

---

## <a id="silent-zero"></a>靜默 0 篇：點解要獨立偵測

同一個模式中過三次：

| 日期 | Source | 幾耐冇人發現 |
|---|---|---|
| 2026-07-15 | 東網 娛樂 | 不明（`error=None` 所以冇人為意） |
| 2026-07-25 | SkyPost 要聞 | 一個月以上 |
| 2026-07-25 | Engadget 中文 | 一個月以上 |

共通點：fetcher 攞到 0 篇但照返 `error=None`（例如當時 `_fetch_skypost` 最尾
無條件 `return articles, None, False`）。於是 build 全綠、每個 stage 都 ok、
`articles.json` 淨係靜靜哋寫住 `effective_count: 0`，冇任何嘢會嘈。**嗰個
fetcher 已隨 source 一齊刪走，但同一個形狀喺其他 fetcher 仍然存在**，所以
2026-07-25 改用一個唔靠個別 fetcher 自覺嘅通用機制。

點解門檻要 24 小時咁鈍：單次 0 篇太常見（上游斷線、深夜冇新聞、HTTP 304），
一有 0 篇就嗌會變狼來了。

---

## <a id="keyword-watch"></a>關鍵字監控：三日之內改咗五次嘅來龍去脈

**2026-07-20 起點**：用戶要求 keyword alert 「即刻 grab 同 push」（原本要等 20
分鐘 build cycle），所以起咗快速通道。3 個「最快 source」係用真實
`articles.json` 計 median 發布間隔揀出嚟——cnBeta 睇落最快但其實係批量導入嘅
burst pattern，false positive，剔走。

**2026-07-20 vault sync 冧咗真 config**：第一版 parser 將 vault note 入面啲說明
文字（非 `#` 開頭）當成關鍵字，`tests/test_build.py` 嘅 dry-run 真係覆寫咗
`config/watch_keywords.txt`（成段中文說明變咗「關鍵字」）。所以先有 `##
關鍵字清單` 標題呢個 fail-closed 檢查。

**2026-07-21 太多重複 alert**：一單交通意外好多 source 報，每篇獨立彈一次。
慢速通道用 AI `cluster_id` 解決；快速通道冇 clustering 可用，改用 keyword
cooldown。Implement 中間搵到真 bug：cooldown 一開始淨係喺 loop 之前計一次
eligible list，同一個 run 入面 3 篇撞正同一 keyword 照樣齊齊送晒（test 寫咗先
發現，`assert 3 == 1`）——所以規則係**逐篇 check + send 完即時更新**。

**2026-07-21 字面太闊**：「死亡」「自殺」「交通意外」撞中海外新聞同歷史人物，
所以有 `context:` directive。之後 2026-07-21 再加暴雨組，因為「紅雨」「黑雨」
成日俾人喺同天氣無關嘅八卦文借用（例如「紅雨可WFH」）。⚠️ 已知唔係 100%
有效：驗證過其中一篇假陽性（「中環人黑雨堅持著皮鞋」）本身就提到「港人」
（講緊體面文化，同天氣完全無關嗰句），依然漏網。加 context 濾走咗 2/3，
用戶接受呢個取捨。

**2026-07-21 Google Trends 併入**：用戶問「可唔可以攞到 google 最近一小時嘅
關鍵字」。Google 冇官方「過去一小時」granularity 嘅 API，兩個選擇同用戶講清
楚咗：unofficial `pytrends`（scrape，ToS 風險）vs 官方 daily trending RSS
（免 auth）。用戶揀後者。原本跟 `daily_brief.py` 做「每日一次」冪等 pattern，
但抽真實 feed 兩次比對發現 item 嘅 pubDate 相隔 10-30 分鐘、個榜好快轉勻——
**「daily」淨係個 feed 個名，唔係實際更新頻率**，所以改做跟 build 同頻率。

探測過用 `ht:approx_traffic`（feed 附帶嘅搜尋量估算）做過濾，抽真實數據先發現
高流量同「值得 alert」完全冇關係：「風球」「六合彩」流量全榜最高（5000+），
但正正係最唔值得即時提醒嘅日常內容。**冇採用。**

**2026-07-21 太密集**：抽咗慢速通道 111 分鐘嘅實際送出記錄（20 個 alert，平均
5.5 分鐘一個）核對，揪到三個病因——持續事件跨 cluster、暴雨字借用、trending
字洗版。所以有 keyword-level cooldown 同 trending 獨立 quota。

**2026-07-22 索性停用慢速通道**：用戶覺得同快速通道職能重疊。留返 module 同
test 冇刪，`SLOW_KEYWORD_ALERTS_ENABLED` 一行 flip 就返到轉頭。

**2026-07-25 發現停用有副作用**：見 [GitHub cron](#github-cron)。

⚠️ **Test isolation 陷阱重複出現咗兩次**（`KEYWORD_CONTEXT` 一次、
`TRENDING_KEYWORDS` 一次）：一旦 self-hosted 機真.跑過 sync，config 檔會有真實
內容並提交入 repo，之後冇 monkeypatch 嘅 test 就會意外撞中真實關鍵字／熱門字。
兩個 test file 已加 autouse fixture 預設清空。

---

## <a id="github-cron"></a>GitHub cron 唔可靠：實測數字

2026-07-25 量度 `fast-watch.yml`（cron 寫住 `*/5`）連續 30 個 run：

```
07-24 15:43Z  gap= 93.9min      07-24 12:08Z  gap= 80.9min
07-24 14:09Z  gap=121.6min      07-24 10:47Z  gap=127.5min
```

以上全部喺 active 時段內（唔關 quiet hours 事）。即係「5 分鐘 tripwire」真身係
1.5–8 小時一次，miss rate 16–26 倍。

點解特別嚴重：慢速通道 2026-07-22 停用之後佢係唯一嘅 keyword 通道，所以
keyword alert 等於靜靜哋失效。加咗本機 dispatch 之後實測間隔變返 5.0 分鐘。

Race guard 嘅由來：2026-06-09 撞過第二個 dispatch 落喺 in-progress run 上面，
會 cancel 緊行緊嗰個（即使 `concurrency.cancel-in-progress` 係 false）。

---

## <a id="yahoo-body"></a>Yahoo 科技：點解要 custom parser

2026-07-25 用戶報告每篇文之後跟住「其他人也在看」嘅無關新聞。查實 Yahoo 個推薦
欄**嵌住成篇完整文章**（唔止連結），所以 trafilatura 一鑊過掃晒——一篇 Claude
Opus 5 嘅科技文，內文夾住自助餐優惠同 LeBron James 轉會。呢啲會餵落
`analyse.py`，摘要／標籤／topic 全部污染。

DOM 調查結果：

| 容器 | 字數 | 有冇雜質 |
|---|---|---|
| `div.article-wrapper` | 4695 | ✗ 含推薦欄 |
| `section.module-article-body` | 658 | ✓ |
| `section.module-article-body div.atoms` | 530 | ✓ 純內文 |

❗**我當時嘅判斷失誤**：加呢個 source 嗰陣淨係量咗字數（2,500-2,900 字）就當
合格——但垃圾正正係**撐大**字數嗰樣嘢，所以個數字睇落先咁健康。清乾淨之後每篇
得 500-1,800 字（多數係短快訊）。**驗 parser 一定要真係讀返抽到嘅文字。**

---

## <a id="removed-sources"></a>已移除來源：點解係「移除」而唔係「修」

**Engadget 中文**（2026-07-25）：`chinese.engadget.com` 連 DNS 都解析唔到，網站
已停運。Yahoo 香港吸收咗佢嘅中文科技內容（Engadget 中文版本身就係 Yahoo 旗下），
所以用 Yahoo 科技頂上。⚠️ 查證過**唔係正式重定向**——Yahoo 個頁面完全冇提過
Engadget，係內容血緣上嘅接班，唔係技術上嘅。

**SkyPost 要聞**（2026-07-25）：晴報轉型做「健康、娛樂、家庭生活資訊頻道」
（首頁 title 自己咁寫），唔再出港聞。實測 `/news/` 同首頁抽到嘅文全部係
健康／副刊 section，冇一篇係 `港聞`，而且最新一篇都係兩星期前。

佢個 sitemap 亦凍結咗喺 2023 年（最大 article id `3614960`，實際站上已去到
`4165870`），所以**連「修好 sitemap」都救唔返**——唔係 parser 壞，係個 source
冇咗新聞。相關 code（`_fetch_skypost`、`_build_skypost_content` 等約 370 行）
2026-07-25 全部刪走，翻查睇 git history。
