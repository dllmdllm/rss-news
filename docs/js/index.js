(function () {
  const categories = ["全部", "新聞", "國際", "外媒", "娛樂", "科技", "網媒", "消閒"];
  const categoryEmoji = {
    "全部": "🗂️",
    "新聞": "🏙️",
    "國際": "🌍",
    "外媒": "🗞️",
    "娛樂": "🎬",
    "科技": "💻",
    "網媒": "📡",
    "消閒": "☕",
  };
  const RECENT_SEARCH_KEY = "search.recent";
  const RECENT_SEARCH_MAX = 5;
  const BRIEF_COLLAPSE_KEY = "brief.collapsed";
  const DISPLAY_WINDOW_MS = 24 * 60 * 60 * 1000;
  let updateController = null;
  let returnSearchFocus = false;

  function eligibleArticles(rows, now = Date.now()) {
    return (Array.isArray(rows) ? rows : []).filter(article => {
      const stamp = Date.parse(article.published_at || article.date);
      return Number.isFinite(stamp) && stamp >= now - DISPLAY_WINDOW_MS && stamp <= now + 5 * 60 * 1000;
    });
  }

  function loadRecentSearches() {
    try {
      const arr = JSON.parse(localStorage.getItem(RECENT_SEARCH_KEY) || "[]");
      return Array.isArray(arr) ? arr.filter((s) => typeof s === "string" && s.trim()).slice(0, RECENT_SEARCH_MAX) : [];
    } catch (_) {
      return [];
    }
  }

  // localStorage access itself (getItem, not just setItem) can throw
  // SecurityError under blocked-cookies/private-mode — this was previously
  // unguarded right here at module init, so it could crash the whole script
  // before bindEvents()/load() ever ran, leaving the page looking "loaded
  // but empty" (2026-07-21 audit finding).
  function storageGet(key, fallback = "") {
    try { return localStorage.getItem(key) || fallback; } catch (_) { return fallback; }
  }

  const state = {
    articles: [],
    loaded: false,
    loadFailed: false,
    topics: [],
    sources: {},
    category: "全部",
    source: "",
    topic: "",
    readingStatus: "all",
    panels: {},
    sentiment: "",   // 今日輿情格撳落去篩（negative / neutral / positive）
    entity: "",      // 今日焦點撳落去篩，格式 "type:name"
    entityIndex: null,  // Map("type:name" -> entity object，攞 article_ids 用
    // 撳 topic chip 之前嘅 category/source snapshot，等「✕ 清除話題」
    // 可以還原返（唔係一律跌落「全部」），見 clearTopic handler。
    preTopicFilter: null,
    openCategories: new Set(),
    mode: "latest",
    desktopView: "list",
    query: "",
    fuse: null,
    recentSearches: loadRecentSearches(),
    mobile: {
      view: storageGet("mobile.view", "home"),
      homeMode: storageGet("mobile.homeMode", "latest"),
      aiMode: storageGet("mobile.aiMode", "priority"),
      homeCat: storageGet("mobile.homeCat", "全部"),
      aiCat: storageGet("mobile.aiCat", "全部"),
      homeSource: storageGet("mobile.homeSource", ""),
      aiSource: storageGet("mobile.aiSource", ""),
    },
  };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

  // localStorage 喺私隱模式／封鎖 cookies 環境可能 throw——寫入失敗唔應該
  // 打斷 view 切換，靜默略過（讀取喺各 call site 自行 try/catch）。
  const storageSet = (key, value) => {
    try { localStorage.setItem(key, value); } catch (_) {}
  };

  // articleUrl, timeLabel, summaryIsTitleFallback, criticalScore live in
  // docs/js/common.js — shared with article-redesign.js.

  function summaryText(article, limit = 120) {
    if (summaryIsTitleFallback(article)) return "";
    const raw = String(article.summary || "").replace(/\\n/g, "\n").replace(/(?:^|\n)\s*[・•●]\s*/g, " ").replace(/\s+/g, " ").trim();
    return raw.length > limit ? raw.slice(0, limit - 1) + "…" : raw;
  }

  function summaryPoints(article, limit = 5) {
    if (summaryIsTitleFallback(article)) return [];
    // MiniMax 偶爾儲咗 literal "\n"（backslash + n），先 normalize 至真 newline。
    // 唔好用 "-" 做分隔符：會炒散「5-4 裁決」「e-sports」呢類內容。
    const raw = String(article.summary || "").replace(/\\n/g, "\n").trim();
    let points = raw
      .split(/\n|\s+(?=[・•●])/)
      .map((line) => line.replace(/^\s*[・•●]\s*/, "").replace(/\s+/g, " ").trim())
      .filter(Boolean);
    if (points.length <= 1 && !/^[・•●]/.test(raw) && !raw.includes('\n')) {
      const text = points[0] || "";
      points = text
        ? text.split(/。|；|;/).map((line) => line.trim()).filter(Boolean)
        : [];
    }
    return points.slice(0, limit);
  }

  function pointsHtml(article, limit = 5) {
    const points = summaryPoints(article, limit);
    if (points.length) return points.map((point) => `<li>${summaryPointHtml(article, point)}</li>`).join("");
    return `<li class="summary-pending">${esc(summaryEmptyText(article))}</li>`;
  }

  function summaryPointHtml(article, point) {
    const helper = window.RssSourceEvidence;
    const support = helper?.summarySupport(article, point);
    return `<span class="summary-point">${esc(point)}</span>` + (support ? '' : ' <span class="summary-unverified">未核實</span>');
  }

  function summarySentencesHtml(article) {
    const points = summaryPoints(article, 5);
    if (points.length) return esc(points.join(" "));
    return `<span class="summary-pending">${esc(summaryEmptyText(article))}</span>`;
  }

  function compactSummaryHtml(article, limit = 3) {
    const points = summaryPoints(article, limit);
    if (points.length) {
      return `<ul>${points.map((point) => `<li>${esc(point)}</li>`).join("")}</ul>`;
    }
    const fallback = summaryText(article, 120);
    return fallback ? `<p>${esc(fallback)}</p>` : `<p class="summary-pending">${esc(summaryEmptyText(article))}</p>`;
  }

  function metaLine(article, extra = "") {
    return [
      article.category || "",
      article.source || "",
      timeLabel(article),
      extra,
    ].filter(Boolean).join(" · ");
  }

  function revisionLabel(article) {
    const label = updateController?.label(article);
    return label ? `<span class="revision-label" title="與這部裝置上次開頁比較">${label === 'new' ? '新增' : '已更新'}</span>` : '';
  }

  function summaryNoticeHtml(article) {
    const text = summaryNotice(article);
    return text ? `<p class="summary-limits">${esc(text)}</p>` : '';
  }

  function translationNotice(article) {
    if (!['deferred', 'failed', 'unavailable', 'partial'].includes(article.translation_status)) return '';
    return '<span class="reading-provenance">內文翻譯未完整；未譯部分保留原文。</span>';
  }

  const groupDetails = article => window.RssStoryGroups?.detailsHtml(article) || '';

  function categoryClass(category) {
    const map = {
      "新聞": "cat-news",
      "國際": "cat-world",
      "外媒": "cat-foreign",
      "娛樂": "cat-ent",
      "科技": "cat-tech",
      "消閒": "cat-life",
      "網媒": "cat-media",
    };
    return map[category] || "";
  }

  function categoryChip(category) {
    return `<span class="chip ${categoryClass(category)}">${esc(category || "未分類")}</span>`;
  }

  function priorityBadge(article) {
    const score = criticalScore(article);
    if (score >= 105) return `<span class="priority-badge high" title="優先度 ${score}">🔥 必讀</span>`;
    if (score >= 80) return `<span class="priority-badge mid" title="優先度 ${score}">⭐ 推薦</span>`;
    return "";
  }

  // headline_fit：AI 評估標題同內文相符度（0-10），<=3 先掛 badge——
  // 寧願放走幾單都唔好冤枉正常標題。null／undefined（舊 cache）唔顯示。
  function clickbaitBadge(article) {
    if (!Number.isInteger(article.headline_fit) || article.headline_fit > 3) return "";
    return `<span class="priority-badge clickbait" title="AI 評估標題同內文相符度 ${article.headline_fit}/10">⚠️ 標題誇大</span>`;
  }

  const SORT_CATEGORY_ORDER = ["新聞", "國際", "外媒", "娛樂", "科技", "消閒", "網媒"];
  function categoryRank(cat) {
    const idx = SORT_CATEGORY_ORDER.indexOf(cat);
    return idx === -1 ? 99 : idx;
  }

  function sortedArticles(input) {
    const arr = [...input];
    if (state.mode === "latest") {
      return arr.sort((a, b) => {
        const byDate = String(b.date || "").localeCompare(String(a.date || ""));
        return byDate || (categoryRank(a.category) - categoryRank(b.category));
      });
    }
    if (state.mode === "balanced") {
      return arr.sort((a, b) => {
        const score = Number(b.score || 0) - Number(a.score || 0);
        const byDate = String(b.date || "").localeCompare(String(a.date || ""));
        return score || byDate || (categoryRank(a.category) - categoryRank(b.category));
      });
    }
    return arr.sort((a, b) => criticalScore(b) - criticalScore(a));
  }

  function filteredArticles() {
    const terms = RssSearch.tokens(state.query);
    // 實體篩選靠 entities.json 記低嘅 article_ids——AI 抽到嘅實體名唔一定
    // 逐字出現喺標題／摘要，自己夾字會漏一大截。
    const entityEntry = state.entity && state.entityIndex
      ? state.entityIndex.get(state.entity)
      : null;
    const entityIds = entityEntry && Array.isArray(entityEntry.article_ids)
      ? new Set(entityEntry.article_ids)
      : null;
    const selectedTopic = state.topic
      ? state.topics.find((topic) => topic.topic === state.topic)
      : null;
    const topicIds = new Set((selectedTopic && selectedTopic.article_ids) || []);
    const matched = state.articles.filter((article) => {
      if (state.topic) {
        const inTopic = topicIds.size
          ? topicIds.has(article.id)
          : article.topic === state.topic;
        if (!inTopic) return false;
      }
      if (state.readingStatus !== "all" && window.RssReaderState && !window.RssReaderState.filter([article], state.readingStatus).length) return false;
      const memberships = article.memberships || [{source: article.source, category: article.category}];
      if (!memberships.some(m => (state.category === '全部' || m.category === state.category) && (!state.source || m.source === state.source))) return false;
      if (state.sentiment && article.sentiment !== state.sentiment) return false;
      if (entityIds && !entityIds.has(article.id)) return false;
      return RssSearch.matches(article, terms);
    }).map(article => {
      if (!state.source && state.category === '全部') return article;
      const membership = (article.memberships || [{source: article.source, category: article.category}])
        .find(m => (!state.source || m.source === state.source) && (state.category === '全部' || m.category === state.category));
      return membership ? {...article, metadata_source: article.source, source: membership.source, category: membership.category} : article;
    });
    state.matchCount = matched.length;
    return sortedArticles(RssSearch.collapse(sortedArticles(matched), state.articles));
  }

  function renderNav() {
    // 來源列表按「該分類實際有幾多篇」統計，唔再按 source 註冊分類——
    // 星島主 feed 註冊喺新聞組，但佢啲娛樂/國際文（url_category 重新分類）
    // 之前喺娛樂組來源清單完全唔出現。
    const SEP = String.fromCharCode(31);
    const countsByCat = new Map();
    for (const a of state.articles) {
      const keys = new Set((a.memberships || [{source:a.source, category:a.category}]).map(m => m.category + SEP + m.source));
      for (const key of keys) countsByCat.set(key, (countsByCat.get(key) || 0) + 1);
    }
    const byCategory = categories.map((cat) => {
      const count = cat === "全部"
        ? state.articles.length
        : state.articles.filter((a) => (a.memberships || [{category:a.category}]).some(m => m.category === cat)).length;
      let entries;
      if (cat === "全部") {
        const counts = new Map();
        for (const article of state.articles) {
          for (const source of new Set((article.memberships || [{source:article.source}]).map(m => m.source))) counts.set(source, (counts.get(source) || 0) + 1);
        }
        entries = [...counts.entries()];
      } else {
        entries = [...countsByCat.entries()]
          .filter(([key]) => key.startsWith(cat + SEP))
          .map(([key, n]) => [key.slice(cat.length + 1), n]);
      }
      const sourceNames = entries
        .filter(([, n]) => n > 0)
        .sort((a, b) => b[1] - a[1])
        .map(([name, sourceCount]) => `<button data-source="${esc(name)}" data-category="${esc(cat)}" class="source-btn ${state.source === name ? "active" : ""}">
            <span>${esc(name)}</span><span>${sourceCount}</span>
          </button>`).join("");
      const open = state.openCategories.has(cat);
      return `<div class="tree-group">
        <button data-tree-category="${esc(cat)}" class="tree-head ${cat === state.category && !state.source ? "active" : ""}">
          <span class="tree-arrow">${open ? "▾" : "▸"}</span>
          <span>${categoryEmoji[cat] ? categoryEmoji[cat] + " " : ""}${esc(cat)}</span>
          <span class="tree-count">${count}</span>
        </button>
        <div class="source-children" ${open ? "" : "hidden"}>${sourceNames}</div>
      </div>`;
    }).join("");
    $("categoryNav").innerHTML = byCategory;
  }

  function renderLead(list) {
    const lead = list[0];
    if (!lead) {
      $("leadStory").innerHTML = "";
      return;
    }
    const image = lead.thumbnail
      ? `<img src="${esc(lead.thumbnail)}" alt="">`
      : "";
    $("leadStory").innerHTML = `
      <a href="${articleUrl(lead)}">
        <div class="lead-media">${image}</div>
        <div class="lead-copy">
          <div class="eyebrow">
            ${categoryChip(lead.category)}
            <span>${esc(lead.source || "")}</span>
            <span>${esc(timeLabel(lead))}</span>
            ${revisionLabel(lead)}
            ${priorityBadge(lead)}
            ${clickbaitBadge(lead)}
          </div>
          <h1>${esc(lead.title || "")}</h1>
          <span class="ai-note">AI 摘要</span>
          <ul class="lead-points">${pointsHtml(lead, 5)}</ul>
          ${summaryNoticeHtml(lead)}
          <span>${window.RssReaderState?.articleState(lead.id)?.read ? "已讀" : "未讀"}</span>
        </div>
      </a>${window.RssEventReading?.provenance(lead) || ""}${translationNotice(lead)}${groupDetails(lead)}`;
  }

  function briefItem(article, label) {
    return `<li><a href="${articleUrl(article)}">
      <span>${categoryChip(label)} ${esc(article.source || "")} · ${esc(timeLabel(article))} ${priorityBadge(article)}</span>
      <strong>${esc(article.title || "")}</strong>
      <ul class="brief-points">${pointsHtml(article, 5)}</ul>
    </a></li>`;
  }

  // 優先排行（同欄上方）已顯示嘅文章唔重複出——排行負責「最重要」，
  // 呢度負責「每類最新」，去重先至各有意義。
  let criticalShownIds = new Set();

  function renderDailyBrief() {
    const groups = ["新聞", "國際", "娛樂"];
    const items = [];
    for (const group of groups) {
      sortedArticles(state.articles.filter(
        (a) => a.category === group && !criticalShownIds.has(a.id)
      )).slice(0, 3)
        .forEach((article) => items.push(briefItem(article, group)));
    }
    $("dailyBrief").innerHTML = items.join("");
  }

  function cardSummaryBlock(article) {
    const points = summaryPoints(article, 5);
    if (points.length) {
      return `<ul class="card-points">${points.map((p) => `<li>${summaryPointHtml(article, p)}</li>`).join("")}</ul>`;
    }
    return `<p class="card-summary">${summarySentencesHtml(article)}</p>`;
  }

  function card(article) {
    // lazy：infinite scroll 每批 append 30 張卡，即刻載晒啲圖好傷流動數據
    const image = article.thumbnail ? `<img src="${esc(article.thumbnail)}" alt="" loading="lazy" decoding="async">` : "";
    return `<div class="reading-card"><a class="card ${categoryClass(article.category)}" href="${articleUrl(article)}">
      <div class="thumb">${image}</div>
      <div>
        <div class="meta">
          ${categoryChip(article.category)}
          <span>${esc(article.source || "")}</span>
          ${article.matched_sources?.length > 1 ? `<span>合併來源：${esc(article.matched_sources.join('、'))}</span>` : ''}
          <span>${esc(timeLabel(article))}</span>
          ${revisionLabel(article)}
          ${priorityBadge(article)}
          ${clickbaitBadge(article)}
          <span>${window.RssReaderState?.articleState(article.id)?.read ? "已讀" : "未讀"}</span>
        </div>
        <h3>${esc(article.title || "")}</h3>
        <span class="ai-note">AI 摘要</span>
        ${cardSummaryBlock(article)}
        ${summaryNoticeHtml(article)}
      </div>
    </a>${window.RssEventReading?.provenance(article) || ""}${translationNotice(article)}${groupDetails(article)}</div>`;
  }

  const CATEGORY_GROUPS = ["新聞", "國際", "外媒", "娛樂", "科技", "消閒", "網媒"];
  const CATEGORY_BASE_PER_GROUP = 4;
  const CATEGORY_MAX_PER_GROUP = 12;

  function renderCategorySections(perGroup = CATEGORY_BASE_PER_GROUP) {
    const sections = CATEGORY_GROUPS.map((group) => {
      const rows = filteredArticles().filter(article => article.id !== feedLeadId && article.category === group).slice(0, perGroup);
      if (!rows.length) return "";
      return `<section class="category-section ${categoryClass(group)}">
        <div class="category-section-head">
          <h3>${esc(group)}</h3>
          <span>${rows.length} 則重點</span>
        </div>
        ${rows.map(card).join("")}
      </section>`;
    }).join("");
    $("feed").innerHTML = sections;
  }

  // Grow 分類重點 until its column height matches the brief column on the
  // right. Without this, a tall brief sticks out below the lead while the
  // category grid ends short, leaving the brief column visually orphaned.
  function fillCategoriesToMatchBrief() {
    if (state.query.trim() || state.topic || state.category !== "全部" || state.source || state.sentiment || state.entity || state.readingStatus !== "all") return;
    if (window.matchMedia && !window.matchMedia("(min-width: 901px)").matches) return;
    const brief = document.querySelector(".brief");
    const feed = $("feed");
    if (!brief || !feed) return;
    let perGroup = CATEGORY_BASE_PER_GROUP;
    let lastFeedH = -1;
    // Iterate a few times, expanding the per-category cap until the category
    // grid is at least as tall as the brief (within 60px tolerance). Bail out
    // early if a re-render produces no growth — that means every category is
    // already saturated and adding more would have no effect.
    for (let i = 0; i < 6 && perGroup < CATEGORY_MAX_PER_GROUP; i++) {
      const briefH = brief.offsetHeight;
      const feedH = feed.offsetHeight;
      if (feedH >= briefH - 60) break;
      if (feedH === lastFeedH) break;
      lastFeedH = feedH;
      perGroup += 2;
      renderCategorySections(perGroup);
    }
  }

  // 平鋪 feed 用 IntersectionObserver 做 infinite scroll：sentinel 入 viewport
  // 就 append 下一批，直到掃完整個 filtered list 為止。renderAll 每次入嚟都
  // disconnect 舊 observer，避免 filter 切咗之後 stale callback 仍會 append。
  const FEED_BATCH = 30;
  let feedObserver = null;
  function disconnectFeedObserver() {
    if (feedObserver) {
      feedObserver.disconnect();
      feedObserver = null;
    }
  }

  function renderFeedFlat(list) {
    const feed = $("feed");
    // Lead 見到先至跳過第一篇；lead 藏起嘅 view（mobile AI）跳過會直接
    // 冇咗嗰篇文。舊邏輯用 state.topic 做判斷，topic view 會 lead + feed
    // 重複顯示同一單。
    const items = feedLeadId ? list.slice(1) : list;
    feed.innerHTML = "";
    disconnectFeedObserver();
    let rendered = 0;
    const restoreIndex = items.findIndex(article => article.id === state.restoreArticleId);
    const appendBatch = () => {
      const next = Math.min(rendered + (rendered === 0 ? Math.max(FEED_BATCH,
        restoreIndex >= 0 ? restoreIndex + 1 + FEED_BATCH : 0, state.restoreFeedCount || 0) : FEED_BATCH), items.length);
      if (next > rendered) {
        const oldSentinel = feed.querySelector(".feed-sentinel");
        if (oldSentinel) oldSentinel.remove();
        const oldEnd = feed.querySelector(".feed-end-note");
        if (oldEnd) oldEnd.remove();
        feed.insertAdjacentHTML("beforeend", items.slice(rendered, next).map(card).join(""));
        rendered = next;
      }
      if (rendered < items.length) {
        const sentinel = document.createElement("div");
        sentinel.className = "feed-sentinel";
        sentinel.setAttribute("aria-hidden", "true");
        feed.appendChild(sentinel);
        if (!feedObserver) {
          feedObserver = new IntersectionObserver((entries) => {
            for (const entry of entries) {
              if (entry.isIntersecting) appendBatch();
            }
          }, { rootMargin: "600px" });
        }
        feedObserver.observe(sentinel);
      } else {
        disconnectFeedObserver();
        if (items.length) {
          const end = document.createElement("div");
          end.className = "feed-end-note";
          end.textContent = "已到底 · 無更多文章";
          feed.appendChild(end);
        }
      }
    };
    appendBatch();
  }

  // 而家個 feed 有冇 render lead 嗰篇（供 renderFeedFlat / sections 跳過用）。
  let feedLeadId = null;

  function renderFeed(list) {
    const feed = $("feed");
    // Lead 喺 desktop（任何 view）同 mobile 時間線先見到；mobile AI view
    // 用 CSS 藏咗 lead。
    const leadVisible = !isMobile() || state.mobile.view === "home";
    feedLeadId = (leadVisible && list[0]) ? list[0].id : null;
    // 「平鋪時間線 vs 分類重點」只應該由手機時間線 view 決定——一定要
    // 加 isMobile() gate，唔係 desktop layout 會被殘留嘅 mobile.view 影響
    // （mobile.view 預設 "home"，冇 gate 嘅話 desktop 永遠行平鋪）。
    const mobileFlat = isMobile() && state.mobile.view === "home";
    if (!state.query.trim() && !mobileFlat && !state.topic && state.category === "全部" && !state.source && !state.sentiment && !state.entity && state.readingStatus === "all") {
      feed.classList.remove("feed-grid");
      disconnectFeedObserver();
      renderCategorySections();
    } else {
      feed.classList.add("feed-grid");
      renderFeedFlat(list);
    }
    // 話題 filter 生效時提供顯眼嘅退出方式——手機由話題 chip 跳過嚟，
    // 冇呢個掣就唔知自己身處 filter 狀態、更加唔知點返出去。
    $("resultCount").innerHTML = state.topic
      ? `${list.length} 篇 <button class="clear-filter" id="clearTopic" type="button">✕ 清除話題</button>`
      : (state.sentiment
        ? `${list.length} 篇 <button class="clear-filter" id="clearSentiment" type="button">✕ 清除輿情</button>`
        : (state.entity
          ? `${list.length} 篇 <button class="clear-filter" id="clearEntity" type="button">✕ 清除焦點</button>`
          : `${state.matchCount} 篇匹配報道 · ${list.length} 張卡`));
    if (state.loaded === false) $('resultCount').textContent = '正在讀取文章資料';
    // 「分類重點」只喺真係 render sections 嗰陣先啱；手機時間線係平鋪，
    // 叫返「最新新聞流」。
    $("feedTitle").textContent = state.source
      ? `${state.source}新聞流`
      : (state.entity
        ? `${state.entity.split(":")[1] || ""} · 焦點`
        : state.sentiment
        ? `${MOOD_META[state.sentiment]?.label || ""}新聞流`
        : state.topic
        ? `${state.topic} · 話題`
        : (state.category === "全部" ? (mobileFlat ? "最新新聞流" : "分類重點") : `${state.category}新聞流`));
  }

  // 同一間媒體嘅唔同版面各有各嘅 feed（明報有 5 個、HK01 有 7 個），但
  // 星島／am730 就係一個 feed 靠 url_category 拆去多個分類。所以「來源健康」
  // 一直將「明報 本地」（一個版面）同「星島頭條」（成間媒體）並排列，粒度
  // 唔一致，仲要 slice 頭 8 個——明報 5 個版面合共 85 篇，拆開之後每個都
  // ≤30，喺榜上完全睇唔到（2026-07-25 用戶反映）。
  const OUTLET_PREFIXES = ["RTHK", "明報", "東網", "HK01", "Now", "HuffPost"];

  function outletOf(sourceName) {
    const name = String(sourceName || "").trim();
    if (/^(星島頭條|星島 即時中國|星島 即時國際)$/.test(name)) return "星島頭條";
    for (const prefix of OUTLET_PREFIXES) {
      if (name.startsWith(prefix + " ")) return prefix;
    }
    // 淨低嗰啲本身就係「成間媒體」（星島頭條／am730／cnBeta／The Witness…），
    // 唔好照空格斬——「New MobileLife」「The Collective HK」會斬到面目全非。
    return name;
  }

  function groupSourcesByOutlet(sources) {
    const rows = new Map();
    for (const [name, info] of Object.entries(sources || {})) {
      const outlet = outletOf(name);
      const count = Number(info?.effective_count ?? info?.count ?? 0);
      const row = rows.get(outlet) || { outlet, count: 0, feeds: 0 };
      row.count += count;
      row.feeds += 1;
      rows.set(outlet, row);
    }
    return [...rows.values()].sort((a, b) => b.count - a.count);
  }

  // Three missed 20-minute publisher cycles: history counts do not prove current health.
  function sourceObservation(info, now = Date.now()) {
    const value = info?.checked_at ?? info?.observed_at;
    const parts = typeof value === "string" && value.match(/^(\d{4})-(\d{2})-(\d{2})T([01]\d|2[0-3]):([0-5]\d):([0-5]\d)(?:\.\d+)?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/i);
    const validDay = parts && Number(parts[2]) >= 1 && Number(parts[2]) <= 12 && Number(parts[3]) >= 1 && Number(parts[3]) <= new Date(Date.UTC(Number(parts[1]), Number(parts[2]), 0)).getUTCDate();
    const stamp = validDay ? Date.parse(value) : NaN;
    if (!Number.isFinite(stamp)) return { status: "unknown", label: "檢查時間未提供或無效", time: "" };
    const time = new Date(stamp).toLocaleString("zh-HK");
    if (stamp > now + 5 * 60 * 1000) return { status: "unknown", label: "檢查時間在未來，狀態未確認", time };
    if (now - stamp > 60 * 60 * 1000) return { status: "stale", label: "觀察已過期", time };
    return { status: "fresh", label: "", time };
  }

  function renderAiPanel(filteredList) {
    // When the user has narrowed to a category / source / topic, the AI
    // workstation should reflect that scope — otherwise the priority list
    // keeps showing global picks the user has filtered away.
    const filterActive = state.query.trim() || state.category !== "全部" || state.source || state.topic || state.sentiment || state.entity || state.readingStatus !== "all";
    const pool = filterActive ? (filteredList || filteredArticles()) : state.articles;
    // Score each article once; reuse for sorting, min/max, and badge rendering.
    const scored = pool.map((article) => ({ article, score: criticalScore(article) }));
    if (filterActive) {
      scored.sort((a, b) => b.score - a.score);
    } else {
      // Honour the user's current sort mode for the unfiltered AI list too.
      const sorted = sortedArticles(pool);
      const order = new Map(sorted.map((article, idx) => [article.id, idx]));
      scored.sort((a, b) => (order.get(a.article.id) ?? Infinity) - (order.get(b.article.id) ?? Infinity));
    }
    const critical = scored.slice(0, 8);
    // 內部分數範圍（「範圍 16-126」）對用戶冇意義——顯示條目數就夠
    $("priorityRange").textContent = critical.length ? `Top ${critical.length}` : "";
    // Compact 形態（方案 C）：排行同「今日 AI 摘要」同住 .brief 一欄，
    // 唔出 bullets——摘要嗰邊先係詳細版；呢度係快速掃描清單。
    criticalShownIds = new Set(critical.map(({ article }) => article.id));
    $("criticalList").innerHTML = critical.map(({ article }) => `
      <a class="ai-pick" href="${articleUrl(article)}">
        <span class="ai-rank-row">
          <span>${esc(metaLine(article))}</span>
          ${priorityBadge(article)}
        </span>
        <strong>${esc(article.title || "")}</strong>
      </a>`).join("");

    $("topicGrid").innerHTML = (state.topics || []).slice(0, 6).map((topic) => `
      <button class="topic ${state.topic === topic.topic ? "active" : ""}" data-topic="${esc(topic.topic || "")}" type="button">
        <strong>${esc(topic.topic || "未分類")}</strong>
        <span>${Number(topic.count || 0)} 篇 · 熱度 ${Math.round(Number(topic.heat || 0))}</span>
      </button>`).join("");

    const sources = groupSourcesByOutlet(state.sources);
    // 冇 <strong>來源健康</strong> 標題：desktop 個 <summary> 同手機設定面板
    // 個 <h2> 都已經係 label，加返就變咗重複兩次（2026-07-25 用戶影相報告
    // 手機設定頁見到兩個「來源健康」——renderMobileSideHealth() 係直接
    // copy 呢段 innerHTML 落已經有 <h2> 嘅 section 度）。
    $("sideSourceHealth").innerHTML = sources.map((row) => `
      <div class="side-health-row">
        <span>${esc(row.outlet)}${row.feeds > 1 ? `<span class="side-health-sub">${row.feeds} 個版面</span>` : ""}</span>
        <span>${row.count} 篇（保留資料）</span>
      </div>`).join("");
    const observations = Object.entries(state.sources || {}).map(([name, info]) => {
      const failed = !!info.error || info.fetch_status === "failed";
      const zero = !failed && info.fetch_status !== "not_modified" && !info.not_modified && Number(info.fresh_count ?? info.count ?? 0) === 0;
      return { name, info, failed, zero, observation: sourceObservation(info) };
    });
    const troubled = observations.filter(row => row.failed || row.zero);
    const stale = observations.filter(row => row.observation.status === "stale");
    const unknown = observations.filter(row => row.observation.status === "unknown");
    $("sideSourceHealth").innerHTML += observations.map(({ name, info, failed, zero, observation }) => {
      const retained = Number(info.effective_count || 0);
      const link = /^https?:\/\//i.test(info.feed_url || "") ? `<a href="${esc(safeUrl(info.feed_url))}">RSS</a>` : "";
      const outcome = info.fetch_status === 'partial' ? '最近一次部分成功，仍有待續抓' : failed ? "最近一次抓取失敗" : zero ? "最近一次抓取 0 篇" : "最近一次抓取成功";
      const status = observation.status === "fresh" ? outcome : `${observation.label}；${outcome}`;
      const history = retained && (failed || zero || observation.status !== "fresh") ? `；保留 ${retained} 篇歷史資料` : "";
      const stamp = `<span class="reading-provenance">檢查時間 ${esc(observation.time || "未提供或無效")}</span>`;
      const reason = String(info.error || "").replace(/\s+/g, " ").trim().slice(0, 140);
      const intake = info.intake;
      const coverage = intake ? (intake.coverage === 'incomplete' ? '收錄未完整、待續抓：請求預算或未讀頁／失敗' : intake.coverage === 'unknown' ? '收錄覆蓋未確認' : '只涵蓋來源提供的索引／feed') : '收錄覆蓋未確認';
      const accounting = intake?.discovered != null ? `；發現 ${intake.discovered}，接收 ${intake.counts.accepted}，排除 ${intake.counts.excluded}，待處理 ${intake.counts.pending}` : '';
      return `<div class="side-health-row" role="status"><span>${esc(name)}：${esc(status)}${history} ${link}${stamp}<span class="reading-provenance">${esc(coverage + accounting)}</span>${reason ? `<span class="reading-provenance">原因：${esc(reason)}</span>` : ""}</span></div>`;
    }).join("");
    const summary = [];
    if (troubled.length) summary.push(`${troubled.length} 個 feed 需要檢查`);
    if (stale.length) summary.push(`${stale.length} 個 feed 觀察已過期`);
    if (unknown.length) summary.push(`${unknown.length} 個 feed 狀態未確認`);
    $("sourceHealth").textContent = summary.join(" · ") || (observations.length ? "來源有回應；收錄範圍見詳情" : "來源狀態未確認");
  }

  function ensureFuse() {
    if (state.fuse || !state.articles.length || typeof Fuse === "undefined") return;
    state.fuse = new Fuse(state.articles, {
      keys: [
        { name: "title", weight: 0.55 },
        { name: "summary", weight: 0.2 },
        { name: "source", weight: 0.1 },
        { name: "tags", weight: 0.15 },
      ],
      threshold: 0.38,
      ignoreLocation: true,
      includeMatches: true,
      minMatchCharLength: 2,
    });
  }

  function aggregateTagCounts(limit = 12) {
    const counts = new Map();
    for (const article of state.articles) {
      for (const tag of (article.tags || [])) {
        if (!tag) continue;
        counts.set(tag, (counts.get(tag) || 0) + 1);
      }
    }
    return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, limit);
  }

  function saveRecentSearch(query) {
    const q = String(query || "").trim();
    if (!q) return;
    const list = [q, ...state.recentSearches.filter((s) => s !== q)].slice(0, RECENT_SEARCH_MAX);
    state.recentSearches = list;
    try { localStorage.setItem(RECENT_SEARCH_KEY, JSON.stringify(list)); } catch (_) {}
    renderSearchRecentChips();
  }

  function clearRecentSearches() {
    state.recentSearches = [];
    try { localStorage.removeItem(RECENT_SEARCH_KEY); } catch (_) {}
    renderSearchRecentChips();
  }

  function renderSearchRecentChips() {
    const host = $("searchRecentChips");
    if (!host) return;
    const clearBtn = $("searchRecentClear");
    if (!state.recentSearches.length) {
      host.innerHTML = `<span class="empty">未有紀錄 — 輸入關鍵字後會自動儲存最近 ${RECENT_SEARCH_MAX} 次</span>`;
      if (clearBtn) clearBtn.style.display = "none";
      return;
    }
    if (clearBtn) clearBtn.style.display = "inline";
    host.innerHTML = state.recentSearches.map((q) => `<button class="recent" data-search-set="${esc(q)}" type="button">${esc(q)}</button>`).join("");
  }

  function renderSearchTopicChips() {
    const host = $("searchTopicChips");
    if (!host) return;
    const list = (state.topics || []).slice(0, 8);
    if (!list.length) {
      host.innerHTML = `<span class="empty">暫無熱門話題</span>`;
      return;
    }
    host.innerHTML = list.map((topic) => `
      <button class="topic" data-search-topic="${esc(topic.topic || "")}" type="button">${esc(topic.topic || "未分類")} <span style="color:var(--muted);">${Number(topic.count || 0)}</span></button>
    `).join("");
  }

  function renderSearchTagChips() {
    const host = $("searchTagChips");
    if (!host) return;
    const list = aggregateTagCounts(12);
    if (!list.length) {
      host.innerHTML = `<span class="empty">暫無標籤</span>`;
      return;
    }
    host.innerHTML = list.map(([tag, count]) => `
      <button class="tag" data-search-set="${esc(tag)}" type="button">${esc(tag)} <span style="color:var(--muted);">${count}</span></button>
    `).join("");
  }

  function highlight(text, query) {
    if (!query) return esc(text);
    const safe = esc(text);
    const tokens = RssSearch.tokens(query).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
    if (!tokens.length) return safe;
    const re = new RegExp(tokens.join("|"), "gi");
    return safe.replace(re, (m) => `<mark>${m}</mark>`);
  }

  function searchResultItem(article, query) {
    return `<a href="${articleUrl(article)}">
      <div class="meta">
        ${categoryChip(article.category)}
        <span>${esc(article.source || "")}</span>
        <span>${esc(timeLabel(article))}</span>
        ${revisionLabel(article)}
        ${priorityBadge(article)}
      </div>
      <strong>${highlight(article.title || "", query)}</strong>
    </a>${groupDetails(article)}`;
  }

  function renderSearchResults() {
    const host = $("searchResultList");
    const title = $("searchResultTitle");
    if (!host || !title) return;
    if (!state.loaded) {
      title.textContent = state.loadFailed ? '搜尋資料載入失敗' : '正在準備搜尋';
      host.innerHTML = state.loadFailed ? '<div class="empty-result" role="status">未取得搜尋資料，請重新載入再試。</div>' : '';
      return;
    }
    const query = state.query.trim();
    if (!query) {
      title.textContent = "建議閱讀";
      const top = filteredArticles()
        .map((article) => ({ article, score: criticalScore(article) }))
        .sort((a, b) => b.score - a.score)
        .slice(0, 8)
        .map(({ article }) => article);
      host.innerHTML = top.length
        ? top.map((a) => searchResultItem(a, "")).join("")
        : `<div class="empty-result">暫無建議</div>`;
      return;
    }
    const results = filteredArticles();
    const signature = JSON.stringify([query, state.category, state.source, state.topic, state.readingStatus,
      state.sentiment, state.entity, state.mode, results.map(a => a.id)]);
    if (signature !== state.searchSignature) {
      state.searchSignature = signature;
      state.searchLimit = state.restoringSnapshot ? Math.max(20, state.searchLimit || 20) : 20;
    }
    if (state.restoringSnapshot) {
      const restoreIndex = results.findIndex(article => article.id === state.restoreArticleId);
      // Leave a page of results below the old anchor so scroll restoration
      // cannot clamp against the shortened mobile search document.
      if (restoreIndex >= 0) state.searchLimit = Math.min(results.length,
        Math.max(state.searchLimit || 20, restoreIndex + 1 + 20));
    }
    const visible = results.slice(0, state.searchLimit || 20);
    title.textContent = `搜尋結果 · ${state.matchCount} 篇匹配報道 · ${results.length} 張卡 · 已顯示 ${visible.length} 張`;
    host.innerHTML = results.length
      ? visible.map(a => searchResultItem(a, query)).join('') + (visible.length < results.length
        ? '<button type="button" data-search-more>載入更多搜尋結果</button>' : '')
      : `<div class="empty-result" role="status">在目前條件及已收錄資料中搵唔到「${esc(query)}」。可調整關鍵字或篩選；未涵蓋未收錄文章。</div>`;

  }

  function renderSearchStage() {
    if (!$("searchStage")) return;
    renderSearchRecentChips();
    renderSearchTopicChips();
    renderSearchTagChips();
    renderSearchResults();
  }

  function renderAll() {
    const list = filteredArticles();
    renderNav();
    renderLead(list);
    // renderAiPanel 要行先：佢會set criticalShownIds，renderDailyBrief
    // 靠佢去重（排行＋摘要而家同住 .brief 一欄）。
    renderAiPanel(list);
    // 輿情用全量文章，唔用 filtered list——否則撳咗「負面」之後個條就變
    // 100% 負面，睇落好似壞咗。個 chip 嘅 active 狀態先反映當前篩選。
    renderMood(state.articles);
    renderDailyBrief();
    renderFeed(list);
    syncDesktopView(list);
    if ($("eventReadingList")) $("eventReadingList").innerHTML = window.RssEventReading?.render(state.topics, list, state.panels) || "";
    renderMobileSideHealth();
    // 來源 chips 靠 state.sources——initial load 時 updateMobileSubUi 行先過
    // data fetch，一定要喺 renderAll 度再 render 一次先會填到內容。
    renderMobileSourceChips();
    renderVersionInfo();
    renderSearchStage();
    // Defer until layout settles, otherwise offsetHeight reads stale numbers.
    requestAnimationFrame(fillCategoriesToMatchBrief);
  }

  function applyFontSize(size) {
    const sizes = { small: "16px", normal: "18px", large: "20px" };
    const next = sizes[size] ? size : "normal";
    document.documentElement.style.fontSize = sizes[next];
    // rss_font_size 係同 article.html 共用嘅偏好；rss_home_font_size 係舊 key，
    // 讀返嚟做 migration（見 bindEvents）。
    storageSet("rss_font_size", next);
    document.querySelectorAll("#fontTools button, #mobileFontTools button").forEach((button) => {
      button.classList.toggle("active", button.dataset.font === next);
    });
  }

  function showHomeOnMobile() {
    if (window.matchMedia && !window.matchMedia("(max-width: 900px)").matches) return;
    switchMobileView("home");
  }

  function syncStateFromMobile() {
    // 來源篩選只喺「分類」mode 生效——「最新」mode 係全部來源嘅時間線。
    if (state.mobile.view === "ai") {
      state.mode = "critical";
      state.topic = "";
      state.category = state.mobile.aiMode === "category" ? state.mobile.aiCat : "全部";
      state.source = state.mobile.aiMode === "category" ? state.mobile.aiSource : "";
    } else if (state.mobile.view === "home") {
      state.mode = "latest";
      state.topic = "";
      state.category = state.mobile.homeMode === "category" ? state.mobile.homeCat : "全部";
      state.source = state.mobile.homeMode === "category" ? state.mobile.homeSource : "";
    }
  }

  function isMobile() {
    return window.matchMedia && window.matchMedia("(max-width: 900px)").matches;
  }

  function updateMobileSubUi() {
    document.querySelectorAll("#mobileSubHome button").forEach((b) => {
      b.classList.toggle("active", b.dataset.timeMode === state.mobile.homeMode);
    });
    document.querySelectorAll("#mobileSubAi button").forEach((b) => {
      b.classList.toggle("active", b.dataset.aiMode === state.mobile.aiMode);
    });
    const showChips =
      (state.mobile.view === "home" && state.mobile.homeMode === "category") ||
      (state.mobile.view === "ai" && state.mobile.aiMode === "category");
    document.body.classList.toggle("cat-chips-on", showChips);
    // AI tab 三個分頁各自對應一個 body class（CSS 喺 index.html 嘅
    // @media (max-width: 900px) 入面）：analysis 顯示 .ai 分析欄收起 .main，
    // priority / category 就喺 .brief 入面二選一顯示。
    const onAi = state.mobile.view === "ai";
    for (const mode of ["priority", "category", "analysis"]) {
      document.body.classList.toggle(`ai-mode-${mode}`, onAi && state.mobile.aiMode === mode);
    }
    renderMobileCatChips();
    renderMobileSourceChips();
  }

  function renderMobileCatChips() {
    const host = $("mobileCatChips");
    if (!host) return;
    const activeCat = state.mobile.view === "ai" ? state.mobile.aiCat : state.mobile.homeCat;
    host.innerHTML = categories.map((cat) => `
      <button data-mobile-cat="${esc(cat)}" class="${cat === activeCat ? "active" : ""}" type="button">${esc(categoryEmoji[cat] || "")} ${esc(cat)}</button>
    `).join("");
  }

  // 分類 chips 下面嗰行來源 chips——手機版做 per-source 篩選嘅入口
  //（桌面版對應功能係左側 tree nav）。
  function renderMobileSourceChips() {
    const host = $("mobileSourceChips");
    if (!host) return;
    const isAi = state.mobile.view === "ai";
    const activeCat = isAi ? state.mobile.aiCat : state.mobile.homeCat;
    const activeSource = isAi ? state.mobile.aiSource : state.mobile.homeSource;
    const counts = new Map();
    for (const article of state.articles) {
      for (const source of new Set((article.memberships || [{source:article.source, category:article.category}])
        .filter(m => activeCat === '全部' || m.category === activeCat).map(m => m.source))) counts.set(source, (counts.get(source) || 0) + 1);
    }
    const sources = [...counts.entries()].map(([source, count]) => [source, {count}])
      .sort((a, b) => Number(b[1].effective_count ?? b[1].count ?? 0) - Number(a[1].effective_count ?? a[1].count ?? 0));
    if (!sources.length) {
      host.innerHTML = "";
      return;
    }
    const chips = [`<button data-mobile-source="" class="${activeSource ? "" : "active"}" type="button">全部來源</button>`];
    for (const [name, src] of sources) {
      chips.push(`<button data-mobile-source="${esc(name)}" class="${name === activeSource ? "active" : ""}" type="button">${esc(name)} <span class="chip-count">${Number(src.effective_count ?? src.count ?? 0)}</span></button>`);
    }
    host.innerHTML = chips.join("");
  }

  // 每個 mobile view 記住自己嘅捲動位置——冇呢個嘅話切 tab 返嚟要由頭碌過。
  const viewScroll = {};

  function switchMobileView(view) {
    const body = document.body;
    const prev = state.mobile.view;
    if (prev && prev !== view) viewScroll[prev] = window.scrollY || 0;
    body.classList.remove("mobile-home", "mobile-ai", "mobile-search", "mobile-settings");
    body.classList.add(`mobile-${view}`);
    state.mobile.view = view;
    storageSet("mobile.view", view);
    document.querySelectorAll("#mobileTabs button").forEach((tab) => {
      tab.classList.toggle("active", tab.dataset.view === view);
    });
    if (view === "search") $("search")?.focus();
    syncStateFromMobile();
    updateMobileSubUi();
    renderAll();
    window.scrollTo({ top: viewScroll[view] || 0, behavior: "auto" });
  }

  // 未揀過 theme 就跟系統（prefers-color-scheme）；揀過以儲存值為準。
  // 唔寫入 localStorage——等「跟系統」用戶將來系統轉色都會跟到。
  function applyThemeInitial() {
    let stored = null;
    try {
      stored = localStorage.getItem(THEME_KEY) || localStorage.getItem("mobile.theme");
    } catch (_) {}
    if (stored) {
      applyTheme(stored);
      return;
    }
    const prefersLight = window.matchMedia && window.matchMedia("(prefers-color-scheme: light)").matches;
    document.body.classList.toggle("theme-light", !!prefersLight);
  }

  function applyTheme(theme) {
    document.body.classList.toggle("theme-light", theme === "light");
    // THEME_KEY ("rss_theme") 定義喺 common.js，同 article / entities /
    // upcoming 頁共用；mobile.theme 係舊 key，寫新 key 時順手清走。
    try {
      localStorage.setItem(THEME_KEY, theme);
      localStorage.removeItem("mobile.theme");
    } catch (_) {}
  }

  function renderMobileSideHealth() {
    const host = $("mobileSideHealth");
    if (!host) return;
    const sourceList = $("sideSourceHealth");
    host.innerHTML = sourceList ? sourceList.innerHTML : "";
  }

  function renderVersionInfo() {
    const node = $("versionInfo");
    if (!node) return;
    const updated = $("sideUpdated")?.textContent || "";
    node.textContent = `資料更新：${updated || "—"}`;
  }

  function bindMobileShell() {
    $("mobileTabs")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-view]");
      if (!button) return;
      switchMobileView(button.dataset.view);
    });
    $("mobileSubHome")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-time-mode]");
      if (!button) return;
      state.mobile.homeMode = button.dataset.timeMode;
      storageSet("mobile.homeMode", state.mobile.homeMode);
      syncStateFromMobile();
      updateMobileSubUi();
      renderAll();
    });
    $("mobileSubAi")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-ai-mode]");
      if (!button) return;
      state.mobile.aiMode = button.dataset.aiMode;
      storageSet("mobile.aiMode", state.mobile.aiMode);
      syncStateFromMobile();
      updateMobileSubUi();
      renderAll();
    });
    $("mobileCatChips")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-mobile-cat]");
      if (!button) return;
      const cat = button.dataset.mobileCat;
      // 轉分類就清走來源篩選——上一個分類嘅來源喺新分類下多數係空 feed。
      if (state.mobile.view === "ai") {
        state.mobile.aiCat = cat;
        state.mobile.aiSource = "";
        storageSet("mobile.aiCat", cat);
        storageSet("mobile.aiSource", "");
      } else {
        state.mobile.homeCat = cat;
        state.mobile.homeSource = "";
        storageSet("mobile.homeCat", cat);
        storageSet("mobile.homeSource", "");
      }
      syncStateFromMobile();
      updateMobileSubUi();
      renderAll();
    });
    $("mobileSourceChips")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-mobile-source]");
      if (!button) return;
      const source = button.dataset.mobileSource || "";
      if (state.mobile.view === "ai") {
        state.mobile.aiSource = source;
        storageSet("mobile.aiSource", source);
      } else {
        state.mobile.homeSource = source;
        storageSet("mobile.homeSource", source);
      }
      syncStateFromMobile();
      updateMobileSubUi();
      renderAll();
    });
    $("mobileFontTools")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-font]");
      if (!button) return;
      applyFontSize(button.dataset.font);
    });
    $("themeToggle")?.addEventListener("click", () => {
      const next = document.body.classList.contains("theme-light") ? "dark" : "light";
      applyTheme(next);
    });
    $("resetMobile")?.addEventListener("click", () => {
      try {
        ["mobile.view", "mobile.homeMode", "mobile.aiMode", "mobile.homeCat", "mobile.aiCat",
         "mobile.homeSource", "mobile.aiSource",
         "mobile.theme", "rss_theme", "rss_home_font_size", "rss_font_size"]
          .forEach((k) => localStorage.removeItem(k));
      } catch (_) {}
      location.reload();
    });
    applyThemeInitial();
    if (isMobile()) {
      switchMobileView(state.mobile.view);
    } else {
      updateMobileSubUi();
    }
    // 跨過 900px breakpoint 時同步 mobile view class：desktop → mobile 冇呢步
    // 嘅話三欄全部 display:none，成頁空白直到 reload。
    const mq = window.matchMedia && window.matchMedia("(max-width: 900px)");
    if (mq && typeof mq.addEventListener === "function") {
      mq.addEventListener("change", (event) => {
        if (event.matches) {
          switchMobileView(state.mobile.view);
        } else {
          document.body.classList.remove("mobile-home", "mobile-ai", "mobile-search", "mobile-settings");
          renderAll();
        }
      });
    }
  }

  function bindEvents() {
    $('keySentenceRetry')?.addEventListener('click', () => { keySentenceFailed = false; loadKeySentences(); });
    $("desktopViewTabs")?.addEventListener("click", event => {
      const button = event.target.closest('[data-desktop-view]');
      if (!button) return;
      state.desktopView = button.dataset.desktopView;
      syncDesktopView();
    });
    $("desktopViewTabs")?.addEventListener("keydown", event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      state.desktopView = event.key === 'Home' ? 'list' : event.key === 'End' ? 'player' : state.desktopView === 'list' ? 'player' : 'list';
      syncDesktopView();
      document.querySelector('#desktopViewTabs [aria-selected="true"]')?.focus();
    });
    $("readingStatus")?.addEventListener("change", event => { state.readingStatus = event.target.value; renderAll(); });
    window.addEventListener("rss-reader-state", event => { if (event.detail?.statusChanged !== false) renderAll(); });
    applyFontSize(storageGet("rss_font_size") || storageGet("rss_home_font_size") || "normal");
    $("categoryNav").addEventListener("click", (event) => {
      const sourceButton = event.target.closest("button[data-source]");
      if (sourceButton) {
        state.category = sourceButton.dataset.category;
        state.source = sourceButton.dataset.source;
        state.topic = "";
        state.openCategories.add(state.category);
        renderAll();
        showHomeOnMobile();
        return;
      }
      const button = event.target.closest("button[data-tree-category]");
      if (!button) return;
      const category = button.dataset.treeCategory;
      const wasOpen = state.openCategories.has(category);
      state.category = category;
      state.source = "";
      state.topic = "";
      if (wasOpen) {
        state.openCategories.delete(category);
      } else {
        state.openCategories.add(category);
      }
      renderAll();
      // Auto-jump to feed only when the picked category has no expandable
      // source list (or it was already expanded). Otherwise let the user see
      // the sources first; a subsequent tap on a source jumps to the feed.
      if (wasOpen || category === "全部") {
        showHomeOnMobile();
      }
    });
    $("topicGrid").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-topic]");
      if (!button) return;
      // 手機要「先切 view、後 set 話題」——switchMobileView 內嘅
      // syncStateFromMobile 會重置 state.topic，順序倒轉就會落地變咗
      // 普通時間線，用戶唔知自己彈咗去邊。
      showHomeOnMobile();
      state.preTopicFilter = { category: state.category, source: state.source };
      state.topic = button.dataset.topic;
      state.category = "全部";
      state.source = "";
      state.query = "";
      $("search").value = "";
      renderAll();
    });
    $("moodBar")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-sentiment]");
      if (!button) return;
      const picked = button.dataset.sentiment;
      // 撳返同一個 = 取消篩選（同 topic chip 一樣嘅 toggle 手感）。
      state.sentiment = state.sentiment === picked ? "" : picked;
      // 同 topic 一樣：手機要先切返 home view，唔係會篩咗但停留喺 AI tab
      // 望住舊內容，用戶唔知發生咩事。
      showHomeOnMobile();
      renderAll();
    });
    $("entityList")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-entity]");
      if (!button) return;
      const picked = button.dataset.entity;
      state.entity = state.entity === picked ? "" : picked;
      showHomeOnMobile();
      renderAll();
    });
    $("modeNav").addEventListener("click", (event) => {
      const button = event.target.closest("button[data-mode]");
      if (!button) return;
      state.mode = button.dataset.mode;
      document.querySelectorAll("#modeNav button").forEach((b) => b.classList.toggle("active", b === button));
      renderAll();
    });
    $("fontTools")?.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-font]");
      if (!button) return;
      applyFontSize(button.dataset.font);
    });
    // Keep the input DOM/focus/caret and AI/navigation intact during typing.
    let searchDebounce = null, composing = false;
    const submitSearch = () => {
      clearTimeout(searchDebounce);
      if (composing) return;
      state.query = $('search').value;
      searchDebounce = setTimeout(() => {
        const list = filteredArticles();
        renderLead(list); renderFeed(list); syncDesktopView(list); renderSearchResults();
        if (!state.query.trim()) requestAnimationFrame(fillCategoriesToMatchBrief);
      }, 100);
    };
    $('search').addEventListener('compositionstart', () => { composing = true; clearTimeout(searchDebounce); });
    $('search').addEventListener('compositionend', () => { composing = false; submitSearch(); });
    $('search').addEventListener('input', submitSearch);
    // 只喺 Enter 先儲「最近搜尋」——blur 都儲嘅話，手機收鍵盤會將打咗
    // 一半嘅 query 入晒紀錄。
    $("search").addEventListener("keydown", (event) => {
      if (event.key === "Enter" && !event.isComposing && !composing) saveRecentSearch(state.query);
    });

    $("searchStage")?.addEventListener("click", (event) => {
      if (event.target.closest('button[data-search-more]')) {
        state.searchLimit = (state.searchLimit || 20) + 20;
        renderSearchResults();
        return;
      }
      const setBtn = event.target.closest("button[data-search-set]");
      if (setBtn) {
        const value = setBtn.dataset.searchSet;
        state.query = value;
        $("search").value = value;
        if (isMobile()) {
          switchMobileView("search");
        }
        saveRecentSearch(value);
        renderAll();
        $("search").focus();
        return;
      }
      const topicBtn = event.target.closest("button[data-search-topic]");
      if (topicBtn) {
        // 同 topicGrid 一樣：先切 view 後 set 話題（見上面註解）
        showHomeOnMobile();
        state.preTopicFilter = { category: state.category, source: state.source };
        state.topic = topicBtn.dataset.searchTopic;
        state.category = "全部";
        state.source = "";
        state.query = "";
        $("search").value = "";
        renderAll();
      }
    });
    $("searchRecentClear")?.addEventListener("click", clearRecentSearches);
    document.addEventListener("click", (event) => {
      if (event.target.closest("#clearTopic")) {
        // 之前一律跌落「全部」——而家還原返撳 topic chip 之前嘅
        // category/source（2026-07-21 audit finding）。
        state.topic = "";
        if (state.preTopicFilter) {
          state.category = state.preTopicFilter.category;
          state.source = state.preTopicFilter.source;
          state.preTopicFilter = null;
        }
        renderAll();
      }
      if (event.target.closest("#clearSentiment")) {
        state.sentiment = "";
        renderAll();
      }
      if (event.target.closest("#clearEntity")) {
        state.entity = "";
        renderAll();
      }
    });
    bindMobileShell();
  }

  // 列表頁用 slim payload（articles_index.json，冇 key_sentences / entities /
  // url 等重 field，體積係 articles.json 一半以下）。舊 schema（得 5 個 field，
  // graph.html 專用年代）或者 fetch 失敗就 fallback 返 articles.json。
  // cache: "no-cache" 行 ETag revalidation——內容冇變時 304，唔使成個 payload
  // 重新下載（舊做法 ?Date.now() + no-store 係每次全量）。
  function payloadIsComplete(payload) {
    // `!{}` 同 `!payload.articles.length`（空 array）喺 JS 都係 false——
    // 一個完全空嘅 sources object / articles array 之前會被當「完整」放行，
    // 唔會 fallback 去 articles.json（2026-07-21 audit finding：build.py
    // 全部 fetch timeout 時可以寫出 sources:{} 但 articles 正常）。
    if (!payload || !Array.isArray(payload.articles) || !payload.trending_topics) return false;
    if (!payload.sources || Object.keys(payload.sources).length === 0) return false;
    if (!payload.articles.length) return false;
    return "summary" in payload.articles[0] && "sentiment" in payload.articles[0];
  }

  async function fetchArticleData() {
    try {
      const res = await fetch("data/articles_index.json", { cache: "no-cache" });
      if (res.ok) {
        const payload = await res.json();
        if (payloadIsComplete(payload)) return payload;
      }
    } catch (_) {}
    const res = await fetch("data/articles.json", { cache: "no-cache" });
    return res.json();
  }

  // ── 今日早報（daily_brief.json）──
  function renderMorningBrief(brief) {
    const host = $("morningBrief");
    if (!host || !brief || !brief.text || !brief.date) return;
    // 早報係朝早嘢：只喺生成當日 00:00 至中午 12:00（HKT）之間顯示，
    // 過咗晏晝就收起（用戶要求）。
    const dayStart = new Date(`${brief.date}T00:00:00+08:00`).getTime();
    const noon = new Date(`${brief.date}T12:00:00+08:00`).getTime();
    const now = Date.now();
    if (!(now >= dayStart && now < noon)) return;
    const parts = String(brief.date).split("-");
    const dateLabel = `${Number(parts[1])}月${Number(parts[2])}日`;
    const highlights = (brief.highlights || []).map((h) => h.id
      ? `<li><a href="article.html?id=${encodeURIComponent(h.id)}">${esc(h.point)}</a></li>`
      : `<li>${esc(h.point)}</li>`).join("");
    // 每單新聞一段：新格式用 \n 分隔；舊格式（一嚿過）fallback 按句號斬
    // ——用戶反映成段 250 字黐埋一嚿好難讀。
    const paras = brief.text.includes("\n")
      ? brief.text.split(/\n+/)
      : brief.text.split(/(?<=。)/).reduce((acc, sentence) => {
          // 每兩句合一段，避免斬得太碎
          if (acc.length && acc[acc.length - 1].split("。").length <= 2) {
            acc[acc.length - 1] += sentence;
          } else {
            acc.push(sentence);
          }
          return acc;
        }, []);
    const textHtml = paras.filter((p) => p.trim())
      .map((p) => `<p>${esc(p.trim())}</p>`).join("");
    host.hidden = false;
    const collapsed = storageGet(BRIEF_COLLAPSE_KEY) === "1";
    host.classList.toggle("collapsed", collapsed);
    host.innerHTML = `
      <div class="morning-brief-head">
        <button class="morning-brief-toggle" id="briefToggle" type="button"
                aria-expanded="${collapsed ? "false" : "true"}" aria-controls="briefBody">
          <span class="morning-brief-caret" aria-hidden="true">▼</span>
          <strong>🌅 今日早報 · ${dateLabel}</strong>
        </button>
        <button class="morning-brief-tts" id="briefTts" type="button">🔊 聽早報</button>
      </div>
      <div class="morning-brief-body" id="briefBody">
        ${brief.title ? `<strong class="morning-brief-title">${esc(brief.title)}</strong>` : ""}
        ${textHtml}
        ${highlights ? `<ul>${highlights}</ul>` : ""}
      </div>`;
    $("briefToggle")?.addEventListener("click", () => {
      const nowCollapsed = host.classList.toggle("collapsed");
      storageSet(BRIEF_COLLAPSE_KEY, nowCollapsed ? "1" : "0");
      $("briefToggle")?.setAttribute("aria-expanded", nowCollapsed ? "false" : "true");
      // 摺埋時仲讀緊就停埋把聲，唔係會有把聲喺度但見唔到停止掣。
      if (nowCollapsed) stopBriefTts();
    });
    $("briefTts")?.addEventListener("click", (e) => {
      e.stopPropagation();
      toggleBriefTts(brief);
    });
  }

  // iOS Safari 靜音之謎（2026-07-25 用戶報告）：舊版將成篇早報（實測 487 字）
  // 塞落一個 utterance。iOS 對長 utterance 會靜靜哋唔出聲或者讀到一半死，
  // 桌面 Chrome 就冇事，所以之前一直冇為意。而家斬做細段順序播。
  const TTS_CHUNK_MAX = 160;
  let briefChunks = [];
  let briefSpeaking = false;
  let briefGeneration = 0;

  function splitForTts(text) {
    const out = [];
    let buf = "";
    // 喺句號類標點斷句，斷唔到（超長句）就硬切，總之唔好超過上限。
    for (const piece of String(text).split(/(?<=[。！？；\n])/)) {
      for (let rest = piece; rest.length; ) {
        const take = rest.slice(0, TTS_CHUNK_MAX);
        rest = rest.slice(TTS_CHUNK_MAX);
        if ((buf + take).length > TTS_CHUNK_MAX && buf) { out.push(buf); buf = ""; }
        buf += take;
      }
    }
    if (buf.trim()) out.push(buf);
    return out.filter((s) => s.trim());
  }

  function pickChineseVoice() {
    // iOS 唔會因為 lang="zh-HK" 就自動揀到粵語聲；voice 留空有機會完全唔出聲。
    // 明確揀一把，粵語優先，跟住台灣／普通話，最後任何中文。
    const voices = window.speechSynthesis.getVoices() || [];
    const byLang = (re) => voices.find((v) => re.test(v.lang || "") || re.test(v.name || ""));
    return byLang(/zh[-_]HK|Cantonese|粵/i)
        || byLang(/zh[-_]TW/i)
        || byLang(/zh[-_]CN/i)
        || byLang(/^zh/i)
        || null;
  }

  function resetBriefTtsBtn() {
    const btn = $("briefTts");
    btn?.classList.remove("active");
    if (btn) btn.textContent = "🔊 聽早報";
  }

  function stopBriefTts() {
    briefGeneration++;
    briefChunks = [];
    briefSpeaking = false;
    if ("speechSynthesis" in window) window.speechSynthesis.cancel();
    resetBriefTtsBtn();
  }

  function speakNextChunk(voice, token = briefGeneration) {
    if (token !== briefGeneration || !briefSpeaking) return;
    if (!briefChunks.length) { stopBriefTts(); return; }
    const utterance = new SpeechSynthesisUtterance(briefChunks.shift());
    if (voice) utterance.voice = voice;
    utterance.lang = voice?.lang || "zh-HK";
    utterance.rate = 1.05;
    utterance.onend = () => { if (briefSpeaking && token === briefGeneration) speakNextChunk(voice, token); };
    utterance.onerror = () => {
      if (token !== briefGeneration) return;
      stopBriefTts();
      const btn = $("briefTts");
      if (btn) btn.textContent = "朗讀失敗，請再試";
    };
    window.speechSynthesis.speak(utterance);
  }

  function toggleBriefTts(brief) {
    if (!("speechSynthesis" in window) || !window.SpeechSynthesisUtterance) {
      const btn = $("briefTts");
      if (btn) { btn.disabled = true; btn.textContent = "呢個瀏覽器未支援朗讀"; }
      return;
    }
    if (briefSpeaking || window.speechSynthesis.speaking) { stopBriefTts(); return; }

    const text = [brief.title, brief.text, ...(brief.highlights || []).map((h) => h.point)]
      .filter(Boolean).join("。");
    briefChunks = splitForTts(text);
    if (!briefChunks.length) return;
    briefSpeaking = true;

    // 一定要喺呢個 click handler 之內同步 call speak()，否則 iOS 當唔係
    // user gesture、直接唔出聲。所以就算 getVoices() 仲係空都照播（用系統
    // 預設聲），唔可以等 voiceschanged。
    speakNextChunk(pickChineseVoice());
    const btn = $("briefTts");
    btn?.classList.add("active");
    if (btn) btn.textContent = "⏹ 停止";
  }

  async function loadMorningBrief() {
    try {
      const res = await fetch("data/daily_brief.json", { cache: "no-cache" });
      if (!res || !res.ok) return;
      renderMorningBrief(await res.json());
    } catch (_) {}
  }

  // ── AI 工作台分析元素（方案 C）：各報矛盾位 + 未來事件 ──
  // 兩份都係 build-time 現成數據（panel_digests / upcoming），零新增成本。
  function claimEvidence(row, side) {
    const id = row["article_id_" + side];
    const url = id ? "article.html?id=" + encodeURIComponent(id) : (/^https?:\/\//i.test(row["url_" + side] || "") ? safeUrl(row["url_" + side]) : "");
    return url ? `<a href="${esc(url)}">查看來源報道</a>` : "";
  }

  function renderContradictions(panelMap) {
    const host = $("contraList");
    const block = $("contraBlock");
    if (!host || !block || !panelMap) return;
    block.hidden = true;
    host.innerHTML = "";
    // Only evidence-backed pairs from the current backend schema are shown.
    const rows = [];
    for (const entry of Object.values(panelMap)) {
      const digest = entry && entry.digest;
      if (!digest) continue;
      const topic = digest.headline || "";
      for (const c of (digest.contradictions || [])) {
        if (c && c.claim_a && c.claim_b && c.quote_a && c.quote_b && c.article_id_a && c.article_id_b &&
          ["a", "b"].every(side => {
            const article = state.articles.find(a => a.id === c["article_id_" + side]);
            return article && c["verified_" + side] === true && /^[a-f0-9]{64}$/.test(c["content_hash_" + side] || "") && c["content_hash_" + side] === article.content_hash;
          })) rows.push({ topic, ...c });
        if (rows.length >= 5) break;
      }
      if (rows.length >= 5) break;
    }
    if (!rows.length) return;
    block.hidden = false;
    host.innerHTML = rows.map((r) => `<div class="contra-item">
           <span class="contra-topic">${esc(r.topic)}</span>
           <span class="src">${esc(r.source_a || "")}</span>：${esc(r.claim_a)}${claimEvidence(r, "a")}<br>
           <span class="src">${esc(r.source_b || "")}</span>：${esc(r.claim_b)}${claimEvidence(r, "b")}
         </div>`).join("");
  }

  function renderTimeline(panelMap) {
    const host = $("timelineList");
    const block = $("timelineBlock");
    if (!host || !block || !panelMap) return;
    // `timeline` 同 tension 一樣：5/7 個 topic 有數據，但前端一直冇讀。
    const groups = [];
    for (const entry of Object.values(panelMap)) {
      const digest = entry && entry.digest;
      const events = (digest && digest.timeline) || [];
      const clean = events.filter((e) => e && e.date && e.event).slice(0, 4);
      if (clean.length >= 2) groups.push({ topic: digest.headline || "", events: clean });
      if (groups.length >= 2) break;
    }
    if (!groups.length) return;
    block.hidden = false;
    host.innerHTML = groups.map((g) => `
      <span class="timeline-topic">${esc(g.topic)}</span>
      ${g.events.map((e) => {
        const d = String(e.date).split("-");
        const label = d.length >= 3 ? `${Number(d[1])}/${Number(d[2])}` : esc(e.date);
        return `<div class="timeline-item"><span class="date">${label}</span><span>${esc(e.event)}</span></div>`;
      }).join("")}`).join("");
  }

  const MOOD_META = {
    negative: { label: "負面", cls: "negative" },
    neutral:  { label: "中性", cls: "neutral" },
    positive: { label: "正面", cls: "positive" },
  };

  function renderMood(list) {
    const block = $("moodBlock");
    const host = $("moodBar");
    if (!block || !host) return;
    // 每篇文都有 sentiment，但 2026-07-25 review 前全站淨係喺文章頁打一行
    // 「情緒: negative」——首頁完全冇用（CLAUDE.md Phase 4 聲稱有「情緒概覽」，
    // 其實從來冇做）。呢格係少數一眼睇得出「AI 幫我讀完晒全部文」嘅呈現。
    const pool = list && list.length ? list : state.articles;
    const counts = { negative: 0, neutral: 0, positive: 0 };
    for (const a of pool) {
      const s = a && a.sentiment;
      if (s in counts) counts[s] += 1;
    }
    const total = counts.negative + counts.neutral + counts.positive;
    if (!total) return;
    block.hidden = false;
    $("moodCount").textContent = `${total} 篇`;
    const order = ["negative", "neutral", "positive"];
    const bar = order.map((k) => {
      const pct = (counts[k] / total) * 100;
      return pct > 0 ? `<span class="mood-seg-${k}" style="width:${pct.toFixed(1)}%"></span>` : "";
    }).join("");
    const legend = order.map((k) => {
      const pct = Math.round((counts[k] / total) * 100);
      const active = state.sentiment === k ? " active" : "";
      return `<button class="mood-chip${active}" data-sentiment="${k}" type="button">
        <span class="mood-dot mood-seg-${k}"></span>${MOOD_META[k].label} ${pct}%
      </button>`;
    }).join("");
    host.innerHTML = `<div class="mood-bar">${bar}</div><div class="mood-legend">${legend}</div>`;
  }

  function renderUpcoming(data) {
    const host = $("upcomingList");
    const block = $("upcomingBlock");
    if (!host || !block || !data || !Array.isArray(data.events)) return;
    const today = (data.today || "").slice(0, 10);
    const events = data.events
      .filter((e) => e && e.date && e.title && e.date >= today)
      .slice(0, 5);
    const uncertain = (data.uncertain_events || []).filter(e => e && e.title).slice(0, 3);
    if (!events.length && !uncertain.length) return;
    block.hidden = false;
    host.innerHTML = events.map((e) => {
      const d = e.date.split("-");
      const article = (e.articles || [])[0];
      const inner = `<span class="date">${Number(d[1])}/${Number(d[2])}</span><span>${esc(e.title)}</span>`;
      return article && article.id
        ? `<a class="upcoming-item" href="article.html?id=${encodeURIComponent(article.id)}">${inner}</a>`
        : `<div class="upcoming-item">${inner}</div>`;
    }).join("") + uncertain.map(e => {
      const article = (e.articles || [])[0];
      const inner = `<span class="date">日期未定</span><span>${esc(e.title)} — ${esc(e.date_label || "有待確認")}</span>`;
      return article?.id ? `<a class="upcoming-item" href="article.html?id=${encodeURIComponent(article.id)}">${inner}</a>` : `<div class="upcoming-item">${inner}</div>`;
    }).join("") + '<p>日期及今日／明日以香港時間為準</p>';
  }

  const ENTITY_TYPE_ICON = { people: "👤", companies: "🏢", places: "📍" };

  function renderEntities(data) {
    const host = $("entityList");
    const block = $("entityBlock");
    if (!host || !block || !data || !Array.isArray(data.entities)) return;
    // 每類挑最多 2 個，湊夠 6 個——唔想成格得曬地點（「香港」128 篇之類
    // 永遠排頭幾位，全部塞晒就冇資訊量）。
    const picked = [];
    for (const type of ["people", "companies", "places"]) {
      const rows = data.entities.filter((e) => e && e.type === type && e.name).slice(0, 2);
      picked.push(...rows);
    }
    if (!picked.length) return;
    state.entityIndex = new Map(picked.map((e) => [`${e.type}:${e.name}`, e]));
    block.hidden = false;
    host.innerHTML = picked.map((e) => {
      const key = `${e.type}:${e.name}`;
      const active = state.entity === key ? " active" : "";
      const digest = String(e.digest || e.summary || "").trim();
      return `<button class="entity-item${active}" data-entity="${esc(key)}" type="button">
        <span class="entity-name">${ENTITY_TYPE_ICON[e.type] || ""} ${esc(e.name)}</span>
        <span class="entity-count">${Number(e.count || 0)} 篇</span>
        ${digest ? `<span class="entity-digest">${esc(digest)}</span>` : ""}
      </button>`;
    }).join("");
  }

  async function loadAiInsights() {
    const jobs = [
      ["panel_digests.json", ["contraBlock", "timelineBlock"], data => { state.panels = data; renderContradictions(data); renderTimeline(data); if ($("eventReadingList")) $("eventReadingList").innerHTML = window.RssEventReading?.render(state.topics, filteredArticles(), data) || ""; }],
      ["upcoming.json", ["upcomingBlock"], renderUpcoming],
      ["entities.json", ["entityBlock"], renderEntities],
    ];
    await Promise.all(jobs.map(async ([file, ids, render]) => {
      const blocks = ids.map(id => $(id)).filter(Boolean);
      const statuses = blocks.map(block => {
        block.hidden = false;
        const status = document.createElement("p");
        status.setAttribute("role", "status");
        status.textContent = "載入分析中…";
        block.appendChild(status);
        return status;
      });
      try {
        const res = await fetch("data/" + file, { cache: "no-cache" });
        if (!res || !res.ok) throw new Error("HTTP " + (res?.status || "network"));
        const data = await res.json();
        if (!data || typeof data !== "object" || Array.isArray(data) ||
            (file === "upcoming.json" && !Array.isArray(data.events)) ||
            (file === "entities.json" && !Array.isArray(data.entities))) throw new Error("Invalid analysis data");
        render(data);
        statuses.forEach((status, i) => {
          const host = blocks[i].querySelector("div");
          if (host?.textContent.trim()) status.remove();
          else status.textContent = "暫時未有分析資料";
        });
      } catch (_) {
        statuses.forEach(status => { status.textContent = "分析載入失敗，請重新載入再試"; });
      }
    }));
  }

  async function load() {
    bindEvents();
    const data = await fetchArticleData();
    const articles = data.articles || [];
    if (!Array.isArray(data.articles)) throw new Error('文章資料格式不符');
    state.articles = eligibleArticles(articles);
    window.RssReaderState?.registerAliases(articles);
    state.topics = data.trending_topics || [];
    state.sources = data.sources || {};
    state.fuse = null;
    state.loaded = true;
    setupSnapshotUpdates(data);
    $("sideUpdated").textContent = data.updated || "";
    $("aiUpdated").textContent = data.updated || "";
    renderAll();
  }

  function captureView() {
    const input = $('search');
    const active = document.activeElement === input;
    const selectors = '#feed a.card, #leadStory > a, #searchResultList > a';
    const anchor = [...document.querySelectorAll(selectors)].find(node => {
      const rect = node.getBoundingClientRect();
      return rect.width > 0 && rect.bottom > 0;
    });
    return {scrollY: window.scrollY, href: anchor?.getAttribute('href'), top: anchor?.getBoundingClientRect().top,
      active, start: input?.selectionStart, end: input?.selectionEnd,
      feedCount: document.querySelectorAll('#feed a.card').length,
      open: [...document.querySelectorAll('details[open][data-story-group]')].map(node => node.dataset.storyGroup)};
  }

  function restoreView(view) {
    for (const node of document.querySelectorAll('details[data-story-group]')) node.open = view.open.includes(node.dataset.storyGroup);
    const anchor = [...document.querySelectorAll('#feed a.card, #leadStory > a, #searchResultList > a')]
      .find(node => node.getAttribute('href') === view.href && node.getBoundingClientRect().width > 0);
    if (anchor && Number.isFinite(view.top)) window.scrollBy(0, anchor.getBoundingClientRect().top - view.top);
    else window.scrollTo(0, view.scrollY);
    if (view.active) {
      $('search')?.focus({preventScroll: true});
      try { $('search').setSelectionRange(view.start, view.end); } catch (_) {}
    }
  }

  function setupSnapshotUpdates(data) {
    if (!window.RssReaderUpdates || updateController) return;
    updateController = window.RssReaderUpdates.create({
      eligible: row => eligibleArticles([row]).length === 1,
      selectArticles: snapshot => eligibleArticles(snapshot.articles),
      onPending: pending => {
        const host = $('updatePrompt');
        if (!host) return;
        host.hidden = !pending;
        if (pending) $('updateMessage').textContent = pending.newCount || pending.updatedCount
          ? `新增 ${pending.newCount} 篇 · 更新 ${pending.updatedCount} 篇` : '資料有更新；目前閱讀保留';
      },
      onError: () => { if ($('updateCheckStatus')) $('updateCheckStatus').textContent = '更新檢查未完成，保留目前文章'; },
      onApply: async snapshot => {
        const view = captureView();
        view.active = view.active || returnSearchFocus;
        returnSearchFocus = false;
        state.restoreArticleId = view.href ? new URL(view.href, document.baseURI).searchParams.get('id') : null;
        state.restoreFeedCount = view.feedCount;
        state.restoringSnapshot = true;
        const overflowAnchor = document.documentElement.style.overflowAnchor;
        document.documentElement.style.overflowAnchor = 'none';
        state.articles = eligibleArticles(snapshot.articles);
        window.RssReaderState?.registerAliases(snapshot.articles);
        state.sources = snapshot.sources;
        state.topics = snapshot.trending_topics || [];
        state.fuse = null;
        keySentenceRecords = new Map(); keySentenceReady = false; keySentenceFailed = false;
        $('sideUpdated').textContent = snapshot.updated || '';
        $('aiUpdated').textContent = snapshot.updated || '';
        renderAll();
        restoreView(view);
        await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
        restoreView(view);
        document.documentElement.style.overflowAnchor = overflowAnchor;
        state.restoreArticleId = null;
        state.restoreFeedCount = 0;
        state.restoringSnapshot = false;
        if ($('updateCheckStatus')) $('updateCheckStatus').textContent = '';
        return true;
      },
    });
    updateController.begin(data);
    updateController.start();
    $('applySnapshotUpdate')?.addEventListener('pointerdown', () => { returnSearchFocus = document.activeElement === $('search'); });
    $('applySnapshotUpdate')?.addEventListener('click', () => void updateController.apply());
    $('dismissSnapshotUpdate')?.addEventListener('click', () => { $('updatePrompt').hidden = true; });
    window.addEventListener('focus', () => void updateController.check());
    window.addEventListener('pagehide', () => updateController.stop());
    // Expose only the read-only/manual refresh controller, not reader data.
    window.rssSnapshotUpdates = updateController;
  }

  let keySentenceRecords = new Map(), keySentenceReady = false, keySentenceFailed = false, keySentenceRequest = null;
  function keySentencePoints(article) {
    const record = keySentenceRecords.get(article.id);
    if (!record || record.title !== article.title || record.source !== (article.metadata_source || article.source) || record.date !== article.date ||
        (article.content_hash && record.content_hash !== article.content_hash) ||
        !record.content_hash || record.key_sentences_content_hash !== record.content_hash || !Array.isArray(record.key_sentences)) return [];
    return [...new Set(record.key_sentences.filter(text => typeof text === 'string' && text.trim()).map(text => text.trim()))];
  }
  function loadKeySentences() {
    if (keySentenceRequest || keySentenceReady || keySentenceFailed) return;
    $('keySentenceRetry').hidden = true;
    summaryPlayer?.setNotice('正在讀取已有關鍵句…');
    // The slim homepage index intentionally omits this field. Read the existing
    // metadata once on demand; never generate AI or fetch per-article bodies.
    keySentenceRequest = (async () => {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      try {
        const response = await fetch('data/articles.json', { cache: 'no-cache', signal: controller.signal });
        if (!response.ok) throw new Error('metadata unavailable');
        const payload = await response.json();
        if (!payload || !Array.isArray(payload.articles)) throw new Error('invalid metadata');
        keySentenceRecords = new Map(payload.articles.filter(row => row && typeof row.id === 'string').map(row => [row.id, row]));
        keySentenceReady = true;
        summaryPlayer?.setNotice('');
        renderAll();
      } catch (_) {
        keySentenceFailed = true;
        summaryPlayer?.setNotice('關鍵句資料讀取失敗，請重試；未使用 AI 摘要代替');
        $('keySentenceRetry').hidden = false;
      } finally { clearTimeout(timer); keySentenceRequest = null; }
    })();
  }
  const summaryPlayer = $("summaryPlayer") && window.RssSummaryPlayer?.create($("summaryPlayer"), {
    points: keySentencePoints, timeLabel,
    pointLink: (article, text) => {
      const record = keySentenceRecords.get(article.id);
      const quote = window.RssSourceEvidence?.keySupport(record, text);
      return quote ? window.RssSourceEvidence.href(record, quote, 'key') : null;
    },
  });
  function syncDesktopView(list) {
    const playing = !isMobile() && state.desktopView === 'player';
    document.body.classList.toggle('summary-mode', playing);
    if ($('summaryPlayer')) $('summaryPlayer').hidden = !playing;
    if (playing) {
      if (keySentenceReady) summaryPlayer?.setArticles(Array.isArray(list) ? list : filteredArticles());
      else loadKeySentences();
      $('feedTitle').textContent = '關鍵句播放';
      $('search').placeholder = '搜尋標題、摘要、來源、標籤';
    } else if ($('feedTitle')?.textContent === '關鍵句播放') {
      renderFeed(filteredArticles());
      $('search').placeholder = '搜尋標題、摘要、來源、標籤';
    }
    document.querySelectorAll('#desktopViewTabs [data-desktop-view]').forEach(button => {
      const selected = button.dataset.desktopView === state.desktopView;
      button.setAttribute('aria-selected', String(selected));
      button.tabIndex = selected ? 0 : -1;
    });
    summaryPlayer?.setActive(playing && keySentenceReady);
  }
  window.matchMedia?.('(max-width: 900px)')?.addEventListener?.('change', () => syncDesktopView());
  load().catch((err) => {
    state.loadFailed = true;
    $("leadStory").innerHTML = `<div class="lead-copy"><h1>載入失敗</h1><p class="summary">${esc(err.message)}</p></div>`;
    $('resultCount').textContent = '文章資料載入失敗';
    renderSearchResults();
  });
  // ── 手機 pull-to-refresh ──
  // PWA / 加入主畫面模式冇瀏覽器 reload 掣，頂部下拉係新聞 app 嘅肌肉記憶。
  // 只喺 scrollY=0 起手先攔截，其餘情況完全唔干預原生捲動。
  function setupPullToRefresh() {
    if (!isMobile() || !("ontouchstart" in window)) return;
    const THRESHOLD = 70;
    let startY = null;
    let indicator = null;

    const ensureIndicator = () => {
      if (indicator) return indicator;
      indicator = document.createElement("div");
      indicator.id = "ptrIndicator";
      indicator.textContent = "↓ 下拉重新整理";
      document.body.appendChild(indicator);
      return indicator;
    };

    window.addEventListener("touchstart", (e) => {
      startY = window.scrollY <= 0 ? e.touches[0].clientY : null;
    }, { passive: true });

    window.addEventListener("touchmove", (e) => {
      if (startY === null) return;
      const delta = e.touches[0].clientY - startY;
      if (delta > 24) {
        const el = ensureIndicator();
        el.classList.add("show");
        const ready = delta > THRESHOLD;
        el.textContent = ready ? "↻ 放手重新整理" : "↓ 下拉重新整理";
        el.classList.toggle("ready", ready);
      }
    }, { passive: true });

    window.addEventListener("touchend", (e) => {
      if (startY === null) return;
      const delta = e.changedTouches[0].clientY - startY;
      startY = null;
      if (indicator) indicator.classList.remove("show");
      if (delta > THRESHOLD && window.scrollY <= 0) {
        if (indicator) {
          indicator.textContent = "⟳ 更新中…";
          indicator.classList.add("show");
        }
        void updateController?.check().finally(() => { if (indicator) indicator.classList.remove('show'); });
      }
    }, { passive: true });
  }

  setupPullToRefresh();
  loadMorningBrief();
  loadAiInsights();

  registerServiceWorker();
}());
