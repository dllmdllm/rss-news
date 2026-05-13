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
  const state = {
    articles: [],
    topics: [],
    sources: {},
    category: "全部",
    source: "",
    topic: "",
    openCategories: new Set(),
    mode: "latest",
    query: "",
  };

  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

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
    const raw = String(article.summary || "").trim();
    let points = raw
      .split(/\n|・|•|●|-/)
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
    const level = score >= 105 ? "high" : (score >= 80 ? "mid" : "");
    return `<span class="priority-badge ${level}">優先度 ${score}</span>`;
  }

  function sortedArticles(input) {
    const arr = [...input];
    if (state.mode === "latest") {
      return arr.sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")));
    }
    if (state.mode === "balanced") {
      return arr.sort((a, b) => {
        const score = Number(b.score || 0) - Number(a.score || 0);
        return score || String(b.date || "").localeCompare(String(a.date || ""));
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
    const byCategory = categories.map((cat) => {
      const count = cat === "全部"
        ? state.articles.length
        : state.articles.filter((a) => a.category === cat).length;
      const sourceNames = Object.entries(state.sources || {})
        .filter(([, src]) => cat === "全部" || src.category === cat)
        .sort((a, b) => Number(b[1].effective_count ?? b[1].count ?? 0) - Number(a[1].effective_count ?? a[1].count ?? 0))
        .map(([name, src]) => {
          const sourceCount = Number(src.effective_count ?? src.count ?? 0);
          return `<button data-source="${esc(name)}" data-category="${esc(cat)}" class="source-btn ${state.source === name ? "active" : ""}">
            <span>${esc(name)}</span><span>${sourceCount}</span>
          </button>`;
        }).join("");
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
    // Hide the cross-category "spotlight" extras when a filter is active.
    // They used to always sample from 國際 / 娛樂 / 科技 across the full feed,
    // so picking a single category (e.g. 科技) showed the lead 科技 article
    // followed by unrelated cross-category extras, making it look like the
    // filter only matched one article. The full filtered list is rendered
    // below by renderFeed anyway.
    const filterActive = state.category !== "全部" || state.source || state.topic;
    const extraArticles = filterActive
      ? []
      : ["國際", "娛樂", "科技"]
          .map((category) => sortedArticles(state.articles.filter((article) => article.category === category && article.id !== lead.id))[0])
          .filter(Boolean);
    const extras = extraArticles
      .map((article) => {
        const image = article.thumbnail
          ? `<img src="${esc(article.thumbnail)}" alt="">`
          : "";
        return `<a class="lead-extra ${categoryClass(article.category)}" href="${articleUrl(article)}">
        <div class="lead-extra-media">${image}</div>
        <div class="lead-extra-copy">
          <span>${categoryChip(article.category)} ${esc(timeLabel(article))}</span>
          ${priorityBadge(article)}
          <strong>${esc(article.title || "")}</strong>
          <div class="lead-extra-summary">${compactSummaryHtml(article, 3)}</div>
        </div>
      </a>`;
      })
      .join("");
    $("leadStory").innerHTML = `
      <a href="${articleUrl(lead)}">
        <div class="lead-media">${image}</div>
        <div class="lead-copy">
          <div class="eyebrow">
            ${categoryChip(lead.category)}
            <span>${esc(lead.source || "")}</span>
            <span>${esc(timeLabel(lead))}</span>
            ${priorityBadge(lead)}
          </div>
          <h1>${esc(lead.title || "")}</h1>
          <p class="summary">${esc(summaryText(lead, 180))}</p>
          <ul class="lead-points">${pointsHtml(lead, 5)}</ul>
        </div>
      </a>
      <div class="lead-extras">${extras}</div>`;
  }

  function briefItem(article, label) {
    return `<li><a href="${articleUrl(article)}">
      <span>${categoryChip(label)} ${esc(article.source || "")} · ${esc(timeLabel(article))} ${priorityBadge(article)}</span>
      <strong>${esc(article.title || "")}</strong>
      <ul class="brief-points">${pointsHtml(article, 5)}</ul>
    </a></li>`;
  }

  function renderDailyBrief() {
    const groups = ["新聞", "國際", "娛樂"];
    const items = [];
    for (const group of groups) {
      sortedArticles(state.articles.filter((a) => a.category === group)).slice(0, 3)
        .forEach((article) => items.push(briefItem(article, group)));
    }
    $("dailyBrief").innerHTML = items.join("");
  }

  function card(article) {
    const image = article.thumbnail ? `<img src="${esc(article.thumbnail)}" alt="">` : "";
    return `<a class="card ${categoryClass(article.category)}" href="${articleUrl(article)}">
      <div class="thumb">${image}</div>
      <div>
        <div class="meta">
          ${categoryChip(article.category)}
          <span>${esc(article.source || "")}</span>
          <span>${esc(timeLabel(article))}</span>
        </div>
        <h3>${esc(article.title || "")}</h3>
        <ul class="card-points">${pointsHtml(article, 3)}</ul>
      </div>
      <div class="rank">${priorityBadge(article)}</div>
    </a>`;
  }

  function renderCategorySections() {
    const groups = ["新聞", "國際", "娛樂", "科技", "消閒", "網媒"];
    const sections = groups.map((group) => {
      const rows = sortedArticles(state.articles.filter((article) => {
        if (article.category !== group) return false;
        if (state.query) {
          const query = state.query.trim().toLowerCase();
          const haystack = `${article.title || ""} ${article.summary || ""} ${article.source || ""} ${(article.tags || []).join(" ")}`.toLowerCase();
          return haystack.includes(query);
        }
        return true;
      })).slice(0, 4);
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

  function renderFeed(list) {
    const feed = $("feed");
    if (!state.topic && state.category === "全部" && !state.source) {
      feed.classList.remove("feed-grid");
      renderCategorySections();
    } else {
      feed.classList.add("feed-grid");
      feed.innerHTML = list.slice(state.topic ? 0 : 1, 31).map(card).join("");
    }
    $("resultCount").textContent = `${list.length} 篇`;
    $("feedTitle").textContent = state.source
      ? `${state.source}新聞流`
      : (state.topic ? `${state.topic} · 話題` : (state.category === "全部" ? "分類重點" : `${state.category}新聞流`));
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
    const critical = scored.slice(0, 10);
    const allScores = scored.map((s) => s.score).filter((score) => Number.isFinite(score));
    const minScore = allScores.length ? Math.min(...allScores) : 0;
    const maxScore = allScores.length ? Math.max(...allScores) : 0;
    $("priorityRange").textContent = allScores.length ? `範圍 ${minScore}-${maxScore}` : "";
    $("criticalList").innerHTML = critical.map(({ article }) => {
      const points = pointsHtml(article, 3);
      return `
      <a class="ai-pick" href="${articleUrl(article)}">
        <span class="ai-rank-row">
          <span>${esc(metaLine(article))}</span>
          ${priorityBadge(article)}
        </span>
        <strong>${esc(article.title || "")}</strong>
        ${points ? `<ul class="ai-pick-points">${points}</ul>` : ""}
      </a>`;
    }).join("");

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

  function renderAll() {
    const list = filteredArticles();
    renderNav();
    renderLead(list);
    renderDailyBrief();
    renderFeed(list);
    renderAiPanel(list);
  }

  function applyFontSize(size) {
    const sizes = { small: "15px", normal: "16px", large: "18px" };
    const next = sizes[size] ? size : "normal";
    document.documentElement.style.fontSize = sizes[next];
    localStorage.setItem("rss_home_font_size", next);
    document.querySelectorAll("#fontTools button").forEach((button) => {
      button.classList.toggle("active", button.dataset.font === next);
    });
  }

  function showHomeOnMobile() {
    if (window.matchMedia && !window.matchMedia("(max-width: 900px)").matches) return;
    const body = document.body;
    if (body.classList.contains("mobile-home")) return;
    body.classList.remove("mobile-ai", "mobile-cats", "mobile-search");
    body.classList.add("mobile-home");
    const tabs = document.querySelectorAll("#mobileTabs button");
    tabs.forEach((tab, idx) => tab.classList.toggle("active", idx === 0));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function bindEvents() {
    applyFontSize(localStorage.getItem("rss_home_font_size") || "normal");
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
      state.topic = button.dataset.topic;
      state.category = "全部";
      state.source = "";
      state.query = "";
      $("search").value = "";
      renderAll();
      showHomeOnMobile();
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
    $("search").addEventListener("input", (event) => {
      state.query = event.target.value;
      renderAll();
    });
  }

  async function load() {
    bindEvents();
    const res = await fetch(`data/articles.json?${Date.now()}`, { cache: "no-store" });
    const data = await res.json();
    state.articles = data.articles || [];
    state.topics = data.trending_topics || [];
    state.sources = data.sources || {};
    $("sideUpdated").textContent = data.updated || "";
    $("aiUpdated").textContent = data.updated || "";
    renderAll();
  }

  load().catch((err) => {
    $("leadStory").innerHTML = `<div class="lead-copy"><h1>載入失敗</h1><p class="summary">${esc(err.message)}</p></div>`;
  });
}());
