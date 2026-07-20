(function () {
  const categories = ["全部", "新聞", "國際", "娛樂", "科技", "網媒", "消閒"];
  const categoryEmoji = {
    "全部": "🗂️",
    "新聞": "🏙️",
    "國際": "🌍",
    "娛樂": "🎬",
    "科技": "💻",
    "網媒": "📡",
    "消閒": "☕",
  };
  const RECENT_SEARCH_KEY = "search.recent";
  const RECENT_SEARCH_MAX = 5;

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
    topics: [],
    sources: {},
    category: "全部",
    source: "",
    topic: "",
    // 撳 topic chip 之前嘅 category/source snapshot，等「✕ 清除話題」
    // 可以還原返（唔係一律跌落「全部」），見 clearTopic handler。
    preTopicFilter: null,
    openCategories: new Set(),
    mode: "latest",
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

  const PENDING_AI_HTML = `<p class="summary-pending">🤖 AI 摘要稍後補上</p>`;

  function summaryText(article, limit = 120) {
    if (summaryIsTitleFallback(article)) return "";
    const raw = String(article.summary || "").replace(/・/g, " ").replace(/\s+/g, " ").trim();
    return raw.length > limit ? raw.slice(0, limit - 1) + "…" : raw;
  }

  function summaryPoints(article, limit = 5) {
    if (summaryIsTitleFallback(article)) return [];
    // MiniMax 偶爾儲咗 literal "\n"（backslash + n），先 normalize 至真 newline。
    // 唔好用 "-" 做分隔符：會炒散「5-4 裁決」「e-sports」呢類內容。
    const raw = String(article.summary || "").replace(/\\n/g, "\n").trim();
    let points = raw
      .split(/\n|・|•|●/)
      .map((line) => line.replace(/\s+/g, " ").trim())
      .filter(Boolean);
    if (points.length <= 1) {
      const text = raw.replace(/\s+/g, " ").trim();
      points = text
        ? text.split(/。|；|;/).map((line) => line.trim()).filter(Boolean)
        : [];
    }
    return points.slice(0, limit);
  }

  function pointsHtml(article, limit = 5) {
    const points = summaryPoints(article, limit);
    if (points.length) return points.map((point) => `<li>${esc(point)}</li>`).join("");
    return summaryIsTitleFallback(article) ? `<li class="summary-pending">🤖 AI 摘要稍後補上</li>` : "";
  }

  function summarySentencesHtml(article) {
    const ks = Array.isArray(article.key_sentences) ? article.key_sentences.filter(Boolean) : [];
    if (ks.length) return esc(ks.slice(0, 5).join(" "));
    const points = summaryPoints(article, 5);
    if (points.length) return esc(points.join(" "));
    if (summaryIsTitleFallback(article)) return `<span class="summary-pending">🤖 AI 摘要稍後補上</span>`;
    return esc(summaryText(article, 220));
  }

  function compactSummaryHtml(article, limit = 3) {
    const points = summaryPoints(article, limit);
    if (points.length) {
      return `<ul>${points.map((point) => `<li>${esc(point)}</li>`).join("")}</ul>`;
    }
    if (summaryIsTitleFallback(article)) return PENDING_AI_HTML;
    const fallback = summaryText(article, 120);
    return fallback ? `<p>${esc(fallback)}</p>` : "";
  }

  function metaLine(article, extra = "") {
    return [
      article.category || "",
      article.source || "",
      timeLabel(article),
      extra,
    ].filter(Boolean).join(" · ");
  }

  function categoryClass(category) {
    const map = {
      "新聞": "cat-news",
      "國際": "cat-world",
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

  const SORT_CATEGORY_ORDER = ["新聞", "國際", "娛樂", "科技", "消閒", "網媒"];
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
    const query = state.query.trim().toLowerCase();
    const selectedTopic = state.topic
      ? state.topics.find((topic) => topic.topic === state.topic)
      : null;
    const topicIds = new Set((selectedTopic && selectedTopic.article_ids) || []);
    return sortedArticles(state.articles.filter((article) => {
      if (state.topic) {
        const inTopic = topicIds.size
          ? topicIds.has(article.id)
          : article.topic === state.topic;
        if (!inTopic) return false;
      }
      if (state.category !== "全部" && article.category !== state.category) return false;
      if (state.source && article.source !== state.source) return false;
      if (!query) return true;
      const haystack = `${article.title || ""} ${article.summary || ""} ${article.source || ""} ${(article.tags || []).join(" ")}`.toLowerCase();
      return haystack.includes(query);
    }));
  }

  function renderNav() {
    // 來源列表按「該分類實際有幾多篇」統計，唔再按 source 註冊分類——
    // 星島主 feed 註冊喺新聞組，但佢啲娛樂/國際文（url_category 重新分類）
    // 之前喺娛樂組來源清單完全唔出現。
    const SEP = String.fromCharCode(31);
    const countsByCat = new Map();
    for (const a of state.articles) {
      const key = a.category + SEP + a.source;
      countsByCat.set(key, (countsByCat.get(key) || 0) + 1);
    }
    const byCategory = categories.map((cat) => {
      const count = cat === "全部"
        ? state.articles.length
        : state.articles.filter((a) => a.category === cat).length;
      let entries;
      if (cat === "全部") {
        entries = Object.entries(state.sources || {})
          .map(([name, src]) => [name, Number(src.effective_count ?? src.count ?? 0)]);
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
            ${priorityBadge(lead)}
            ${clickbaitBadge(lead)}
          </div>
          <h1>${esc(lead.title || "")}</h1>
          <ul class="lead-points">${pointsHtml(lead, 5)}</ul>
        </div>
      </a>`;
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
      return `<ul class="card-points">${points.map((p) => `<li>${esc(p)}</li>`).join("")}</ul>`;
    }
    return `<p class="card-summary">${summarySentencesHtml(article)}</p>`;
  }

  function card(article) {
    // lazy：infinite scroll 每批 append 30 張卡，即刻載晒啲圖好傷流動數據
    const image = article.thumbnail ? `<img src="${esc(article.thumbnail)}" alt="" loading="lazy" decoding="async">` : "";
    return `<a class="card ${categoryClass(article.category)}" href="${articleUrl(article)}">
      <div class="thumb">${image}</div>
      <div>
        <div class="meta">
          ${categoryChip(article.category)}
          <span>${esc(article.source || "")}</span>
          <span>${esc(timeLabel(article))}</span>
          ${priorityBadge(article)}
          ${clickbaitBadge(article)}
        </div>
        <h3>${esc(article.title || "")}</h3>
        ${cardSummaryBlock(article)}
      </div>
    </a>`;
  }

  const CATEGORY_GROUPS = ["新聞", "國際", "娛樂", "科技", "消閒", "網媒"];
  const CATEGORY_BASE_PER_GROUP = 4;
  const CATEGORY_MAX_PER_GROUP = 12;

  function renderCategorySections(perGroup = CATEGORY_BASE_PER_GROUP) {
    const sections = CATEGORY_GROUPS.map((group) => {
      const rows = sortedArticles(state.articles.filter((article) => {
        // Lead 已經喺上面大卡顯示咗，唔好喺分類 section 再出一次。
        if (feedLeadId && article.id === feedLeadId) return false;
        if (article.category !== group) return false;
        if (state.query) {
          const query = state.query.trim().toLowerCase();
          const haystack = `${article.title || ""} ${article.summary || ""} ${article.source || ""} ${(article.tags || []).join(" ")}`.toLowerCase();
          return haystack.includes(query);
        }
        return true;
      })).slice(0, perGroup);
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
    if (state.topic || state.category !== "全部" || state.source) return;
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
    const appendBatch = () => {
      const next = Math.min(rendered + FEED_BATCH, items.length);
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
    if (!mobileFlat && !state.topic && state.category === "全部" && !state.source) {
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
      : `${list.length} 篇`;
    // 「分類重點」只喺真係 render sections 嗰陣先啱；手機時間線係平鋪，
    // 叫返「最新新聞流」。
    $("feedTitle").textContent = state.source
      ? `${state.source}新聞流`
      : (state.topic
        ? `${state.topic} · 話題`
        : (state.category === "全部" ? (mobileFlat ? "最新新聞流" : "分類重點") : `${state.category}新聞流`));
  }

  function renderAiPanel(filteredList) {
    // When the user has narrowed to a category / source / topic, the AI
    // workstation should reflect that scope — otherwise the priority list
    // keeps showing global picks the user has filtered away.
    const filterActive = state.category !== "全部" || state.source || state.topic;
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

    const sources = Object.entries(state.sources || {})
      .sort((a, b) => Number(b[1].effective_count ?? b[1].count ?? 0) - Number(a[1].effective_count ?? a[1].count ?? 0))
      .slice(0, 8);
    $("sideSourceHealth").innerHTML = `<strong>來源健康</strong>` + sources.map(([name, source]) => `
      <div class="side-health-row">
        <span>${esc(name)}</span>
        <span>${Number(source.effective_count ?? source.count ?? 0)} 篇</span>
      </div>`).join("");
    const zero = Object.values(state.sources || {}).filter((s) => Number(s.effective_count ?? s.count ?? 0) === 0).length;
    $("sourceHealth").textContent = zero ? `${zero} 個來源暫時空` : "來源正常";
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
    const tokens = query.split(/\s+/).filter((t) => t.length >= 2).map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"));
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
        ${priorityBadge(article)}
      </div>
      <strong>${highlight(article.title || "", query)}</strong>
    </a>`;
  }

  function renderSearchResults() {
    const host = $("searchResultList");
    const title = $("searchResultTitle");
    if (!host || !title) return;
    const query = state.query.trim();
    if (!query) {
      title.textContent = "建議閱讀";
      const top = [...state.articles]
        .map((article) => ({ article, score: criticalScore(article) }))
        .sort((a, b) => b.score - a.score)
        .slice(0, 8)
        .map(({ article }) => article);
      host.innerHTML = top.length
        ? top.map((a) => searchResultItem(a, "")).join("")
        : `<div class="empty-result">暫無建議</div>`;
      return;
    }
    ensureFuse();
    let results = [];
    if (state.fuse) {
      results = state.fuse.search(query, { limit: 20 }).map((r) => r.item);
    } else {
      const lower = query.toLowerCase();
      results = state.articles.filter((article) => {
        const haystack = `${article.title || ""} ${article.summary || ""} ${article.source || ""} ${(article.tags || []).join(" ")}`.toLowerCase();
        return haystack.includes(lower);
      });
    }
    title.textContent = `搜尋結果 · ${results.length} 篇`;
    host.innerHTML = results.length
      ? results.slice(0, 20).map((a) => searchResultItem(a, query)).join("")
      : `<div class="empty-result">冇結果 — 試吓上面嘅熱門話題或標籤</div>`;
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
    renderDailyBrief();
    renderFeed(list);
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
    const sources = Object.entries(state.sources || {})
      .filter(([, src]) => activeCat === "全部" || src.category === activeCat)
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
    // Debounce：renderAll 會全頁重砌（nav / lead / feed / AI panel），
    // 500+ 篇文加 Fuse 每個 keystroke 跑一次會令手機打字窒。
    let searchDebounce = null;
    $("search").addEventListener("input", (event) => {
      state.query = event.target.value;
      clearTimeout(searchDebounce);
      searchDebounce = setTimeout(renderAll, 150);
    });
    // 只喺 Enter 先儲「最近搜尋」——blur 都儲嘅話，手機收鍵盤會將打咗
    // 一半嘅 query 入晒紀錄。
    $("search").addEventListener("keydown", (event) => {
      if (event.key === "Enter") saveRecentSearch(state.query);
    });

    $("searchStage")?.addEventListener("click", (event) => {
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
    return "summary" in payload.articles[0];
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
    host.innerHTML = `
      <div class="morning-brief-head">
        <strong>🌅 今日早報 · ${dateLabel}</strong>
        <button class="morning-brief-tts" id="briefTts" type="button">🔊 聽早報</button>
      </div>
      ${brief.title ? `<strong class="morning-brief-title">${esc(brief.title)}</strong>` : ""}
      ${textHtml}
      ${highlights ? `<ul>${highlights}</ul>` : ""}`;
    $("briefTts")?.addEventListener("click", () => toggleBriefTts(brief));
  }

  function toggleBriefTts(brief) {
    if (!("speechSynthesis" in window)) return;
    const btn = $("briefTts");
    const resetBtn = () => {
      btn?.classList.remove("active");
      if (btn) btn.textContent = "🔊 聽早報";
    };
    if (window.speechSynthesis.speaking) {
      window.speechSynthesis.cancel();
      resetBtn();
      return;
    }
    const text = [brief.title, brief.text, ...(brief.highlights || []).map((h) => h.point)]
      .filter(Boolean).join("。");
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "zh-HK";
    utterance.rate = 1.05;
    utterance.onend = resetBtn;
    utterance.onerror = resetBtn;
    window.speechSynthesis.speak(utterance);
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
  function renderContradictions(panelMap) {
    const host = $("contraList");
    const block = $("contraBlock");
    if (!host || !block || !panelMap) return;
    const rows = [];
    for (const entry of Object.values(panelMap)) {
      const digest = entry && entry.digest;
      if (!digest) continue;
      for (const c of (digest.contradictions || [])) {
        if (c && c.claim_a && c.claim_b) rows.push({ topic: digest.headline || "", ...c });
        if (rows.length >= 4) break;
      }
      if (rows.length >= 4) break;
    }
    if (!rows.length) return;
    block.hidden = false;
    host.innerHTML = rows.map((r) => `
      <div class="contra-item">
        <span class="contra-topic">${esc(r.topic)}</span>
        <span class="src">${esc(r.source_a || "")}</span>：${esc(r.claim_a)}<br>
        <span class="src">${esc(r.source_b || "")}</span>：${esc(r.claim_b)}
      </div>`).join("");
  }

  function renderUpcoming(data) {
    const host = $("upcomingList");
    const block = $("upcomingBlock");
    if (!host || !block || !data || !Array.isArray(data.events)) return;
    const today = (data.today || "").slice(0, 10);
    const events = data.events
      .filter((e) => e && e.date && e.title && e.date >= today)
      .slice(0, 5);
    if (!events.length) return;
    block.hidden = false;
    host.innerHTML = events.map((e) => {
      const d = e.date.split("-");
      const article = (e.articles || [])[0];
      const inner = `<span class="date">${Number(d[1])}/${Number(d[2])}</span><span>${esc(e.title)}</span>`;
      return article && article.id
        ? `<a class="upcoming-item" href="article.html?id=${encodeURIComponent(article.id)}">${inner}</a>`
        : `<div class="upcoming-item">${inner}</div>`;
    }).join("");
  }

  async function loadAiInsights() {
    try {
      const [panelRes, upRes] = await Promise.all([
        fetch("data/panel_digests.json", { cache: "no-cache" }).catch(() => null),
        fetch("data/upcoming.json", { cache: "no-cache" }).catch(() => null),
      ]);
      if (panelRes && panelRes.ok) renderContradictions(await panelRes.json());
      if (upRes && upRes.ok) renderUpcoming(await upRes.json());
    } catch (_) {}
  }

  async function load() {
    bindEvents();
    const data = await fetchArticleData();
    state.articles = data.articles || [];
    state.topics = data.trending_topics || [];
    state.sources = data.sources || {};
    state.fuse = null;
    ensureFuse();
    $("sideUpdated").textContent = data.updated || "";
    $("aiUpdated").textContent = data.updated || "";
    renderAll();
  }

  load().catch((err) => {
    $("leadStory").innerHTML = `<div class="lead-copy"><h1>載入失敗</h1><p class="summary">${esc(err.message)}</p></div>`;
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
        location.reload();
      }
    }, { passive: true });
  }

  setupPullToRefresh();
  loadMorningBrief();
  loadAiInsights();

  registerServiceWorker();
}());
