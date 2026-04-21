const CATS = ["全部", "新聞", "國際", "娛樂", "消閒", "科技", "網媒"];
const _CAT_WL = new Set(["新聞", "國際", "娛樂", "消閒", "科技", "網媒"]);
const SORTS = [
  ["date", "最新"],
  ["score", "最重要"],
  ["ai", "AI"],
];

let all = [], activeCat = localStorage.getItem("rss_category") || "全部";
let activeSources = new Set();
let activeTag = "", activeTopic = "", sortMode = localStorage.getItem("rss_tab") || "date";
let loadedIds = new Set(), pendingNew = new Set(), pendingData = null;
let trendingTopics = [], sourceStats = {}, sourceCounts = {};
let fuse = null, searchQuery = "", selectedId = "", compactFeed = localStorage.getItem("rss_compact_feed") === "1";
let shareItem = null, kbIndex = -1;

function catClass(c) { return _CAT_WL.has(c) ? "cat-" + c : ""; }
function isDesktop() { return window.matchMedia("(min-width: 768px)").matches; }
function validArticleId(id) { return /^[0-9a-f]{1,32}$/i.test(id || ""); }

setupFontSize();
initChrome();

const READ_KEY = "rss_read_ids";
function getRead() {
  try { return new Set(JSON.parse(localStorage.getItem(READ_KEY) || "[]")); }
  catch { return new Set(); }
}
function setRead(reads) {
  localStorage.setItem(READ_KEY, JSON.stringify([...reads]));
}
function markRead(id) {
  if (!id) return;
  const reads = getRead();
  if (!reads.has(id)) {
    reads.add(id);
    setRead(reads);
    updateUnreadCount();
  }
}

function iconRefresh() {
  return `<svg width="17" height="17" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1"><path d="M21 12a9 9 0 11-2.64-6.36"/><path d="M21 3v6h-6"/></svg>`;
}

function initChrome() {
  document.getElementById("feed-column").classList.toggle("compact", compactFeed);
  document.getElementById("density-toggle").addEventListener("click", () => {
    compactFeed = !compactFeed;
    localStorage.setItem("rss_compact_feed", compactFeed ? "1" : "0");
    document.getElementById("feed-column").classList.toggle("compact", compactFeed);
  });

  document.getElementById("updated").addEventListener("click", e => {
    if (e.target.tagName === "SPAN" && e.target.title) openHealthModal();
    else checkUpdates();
  });
  document.getElementById("mobile-refresh").addEventListener("click", checkUpdates);
  document.getElementById("mobile-notify").addEventListener("click", openHealthModal);

  document.getElementById("search").addEventListener("input", e => {
    searchQuery = e.target.value.trim();
    document.getElementById("clear-search").classList.toggle("show", Boolean(searchQuery));
    activeTopic = "";
    renderFiltered();
  });
  document.getElementById("clear-search").addEventListener("click", () => {
    document.getElementById("search").value = "";
    searchQuery = "";
    document.getElementById("clear-search").classList.remove("show");
    renderFiltered();
  });

  document.getElementById("open-search").addEventListener("click", () => openSearchOverlay());
  document.getElementById("close-search").addEventListener("click", closeSearchOverlay);
  document.getElementById("mobile-search").addEventListener("input", e => renderMobileSearch(e.target.value.trim()));

  document.getElementById("health-close").addEventListener("click", () => closeSheet("health-overlay"));
  document.getElementById("health-overlay").addEventListener("click", e => {
    if (e.target.id === "health-overlay") closeSheet("health-overlay");
  });
  document.getElementById("share-close").addEventListener("click", () => closeSheet("share-overlay"));
  document.getElementById("share-overlay").addEventListener("click", e => {
    if (e.target.id === "share-overlay") closeSheet("share-overlay");
  });

  document.querySelector(".mobile-bottom-nav").addEventListener("click", e => {
    const action = e.target.closest("[data-mobile-action]")?.dataset.mobileAction;
    if (!action) return;
    document.querySelectorAll(".nav-item").forEach(btn => btn.classList.remove("active"));
    e.target.closest(".nav-item").classList.add("active");
    if (action === "sources") openHealthModal();
    if (action === "saved") renderMobileSaved();
    if (action === "settings") openHealthModal();
    if (action === "home") renderFiltered();
  });

  document.getElementById("toast-close").addEventListener("click", () => {
    pendingNew.forEach(id => loadedIds.add(id));
    pendingNew.clear();
    pendingData = null;
    hideToast();
  });
  document.getElementById("toast-refresh").addEventListener("click", () => {
    hideToast();
    applyPending();
  });
}

function showToast(count) {
  document.getElementById("toast-msg").textContent = `${count} 篇新文章`;
  document.getElementById("news-toast").classList.add("show");
}
function hideToast() { document.getElementById("news-toast").classList.remove("show"); }

function applyPending() {
  if (!pendingData) return;
  const snap = new Set(pendingNew);
  pendingNew.clear();
  all = pendingData.articles || [];
  trendingTopics = pendingData.trending_topics || [];
  sourceStats = pendingData.sources || {};
  loadedIds = new Set(all.map(a => a.id));
  pendingData = null;
  rebuildDerivedState();
  renderAll();
  setTimeout(() => {
    snap.forEach(id => {
      const item = document.querySelector(`[data-article-id="${CSS.escape(id)}"]`);
      if (item) item.style.boxShadow = "0 0 0 2px rgba(34,197,94,.35)";
    });
  }, 80);
}

async function load() {
  try {
    renderSkeletons();
    const res = await fetch("data/articles.json?" + Date.now());
    const data = await res.json();
    updateHeader(data);
    all = data.articles || [];
    trendingTopics = data.trending_topics || [];
    sourceStats = data.sources || {};
    loadedIds = new Set(all.map(a => a.id));
    rebuildDerivedState();
    buildFilters();
    renderAll();
  } catch {
    document.getElementById("grid").innerHTML = '<div class="empty">載入失敗，請重試</div>';
    document.getElementById("mobile-list").innerHTML = '<div class="empty">載入失敗，請重試</div>';
  }
}

function rebuildDerivedState() {
  sourceCounts = {};
  all.forEach(a => { sourceCounts[a.source] = (sourceCounts[a.source] || 0) + 1; });
  if (!activeSources.size) activeSources = new Set(Object.keys(sourceCounts));
  activeSources = new Set([...activeSources].filter(s => sourceCounts[s]));
  if (!activeSources.size) activeSources = new Set(Object.keys(sourceCounts));
  fuse = new Fuse(all, {
    keys: ["title", "source", "tags", "summary", "category", "topic"],
    threshold: 0.4,
    minMatchCharLength: 2,
  });
  updateUnreadCount();
}

function updateHeader(data) {
  const updEl = document.getElementById("updated");
  const failed = Object.values(data.sources || {}).filter(s => s.error).length;
  const dot = failed ? ` <span title="${esc(failed + " 個來源失敗")}" style="color:#ef4444">●</span>` : "";
  updEl.innerHTML = esc(data.updated || "") + dot;
}

function updateUnreadCount() {
  const reads = getRead();
  const unread = all.filter(a => !reads.has(a.id)).length;
  document.getElementById("unread-count").textContent = `${unread} 篇未讀`;
  document.getElementById("mobile-notif-dot").style.display = unread ? "" : "none";
}

function renderSkeletons() {
  const feed = Array.from({ length: 6 }, () => `<div class="feed-item">
    <div class="feed-content"><div class="skeleton" style="height:10px;width:38%;margin-bottom:10px"></div><div class="skeleton" style="height:15px;width:94%;margin-bottom:7px"></div><div class="skeleton" style="height:15px;width:70%;margin-bottom:12px"></div><div class="skeleton" style="height:9px;width:28%"></div></div>
    <div class="skeleton feed-thumb"></div>
  </div>`).join("");
  const mobile = Array.from({ length: 5 }, () => `<div class="mobile-card">
    <div class="mobile-card-main"><div class="mobile-card-content"><div class="skeleton" style="height:10px;width:40%;margin-bottom:10px"></div><div class="skeleton" style="height:14px;width:95%;margin-bottom:6px"></div><div class="skeleton" style="height:14px;width:74%;margin-bottom:12px"></div><div class="skeleton" style="height:9px;width:30%"></div></div><div class="skeleton thumb"></div></div>
  </div>`).join("");
  document.getElementById("grid").innerHTML = feed;
  document.getElementById("mobile-list").innerHTML = mobile;
}

function sourceHealth(name) {
  const s = sourceStats[name];
  if (!s) return { cls: "health-ok", off: false };
  const effectiveCount = Number(s.effective_count ?? s.count) || 0;
  if (s.error) return { cls: "health-bad", off: true };
  if (effectiveCount === 0 && !s.not_modified) return { cls: "health-warn", off: false };
  return { cls: "health-ok", off: false };
}

function buildFilters() {
  renderCategories();
  renderSortButtons();
  buildSourceFilters();
  buildTagFilters();
  buildTrendingTopics();
}

function renderCategories() {
  const filters = document.getElementById("filters");
  filters.innerHTML = CATS.map(c => `<button class="category-btn${activeCat === c ? " active" : ""}" data-cat="${esc(c)}">${esc(c)}</button>`).join("");
  filters.onclick = e => {
    const btn = e.target.closest(".category-btn");
    if (!btn) return;
    activeCat = btn.dataset.cat;
    localStorage.setItem("rss_category", activeCat);
    activeTag = "";
    activeTopic = "";
    renderCategories();
    buildTagFilters();
    buildTrendingTopics();
    renderFiltered();
  };
}

function renderSortButtons() {
  const html = SORTS.map(([key, label]) => `<button class="sort-btn${sortMode === key ? " active" : ""}" data-sort="${key}">${label}</button>`).join("");
  document.getElementById("sort-toggle").innerHTML = html;
  document.getElementById("mobile-tabs").innerHTML = html + '<button class="mobile-source-link" type="button">來源狀態</button>';

  document.getElementById("sort-toggle").onclick = sortClick;
  document.getElementById("mobile-tabs").onclick = e => {
    if (e.target.closest(".mobile-source-link")) {
      openHealthModal();
      return;
    }
    sortClick(e);
  };
}

function sortClick(e) {
  const btn = e.target.closest(".sort-btn");
  if (!btn) return;
  sortMode = btn.dataset.sort;
  localStorage.setItem("rss_tab", sortMode);
  renderSortButtons();
  renderFiltered();
}

function buildSourceFilters() {
  const container = document.getElementById("source-filters");
  const sources = Object.keys(sourceCounts).sort();
  container.innerHTML = sources.map(s => {
    const active = activeSources.has(s);
    const health = sourceHealth(s);
    return `<button class="source-toggle${active ? "" : " disabled"}" data-source="${esc(s)}">
      <span class="source-left"><span class="source-dot ${health.off ? "off" : ""}"></span><span class="source-name">${esc(s)}</span></span>
      <span class="source-count">${Number(sourceCounts[s]) || 0}</span>
    </button>`;
  }).join("");
  container.onclick = e => {
    const btn = e.target.closest(".source-toggle");
    if (!btn) return;
    const src = btn.dataset.source;
    if (activeSources.has(src) && activeSources.size > 1) activeSources.delete(src);
    else activeSources.add(src);
    activeTopic = "";
    buildSourceFilters();
    buildTrendingTopics();
    renderFiltered();
  };
}

function topTagsForCategory(articles, category) {
  const tagCounts = {};
  const scope = category === "全部"
    ? articles
    : articles.filter(a => a.category === category);
  scope.forEach(a => (a.tags || []).forEach(t => { tagCounts[t] = (tagCounts[t] || 0) + 1; }));
  return Object.entries(tagCounts)
    .filter(([, c]) => c >= 2).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([t]) => t);
}

function buildTagFilters() {
  const tags = topTagsForCategory(all, activeCat);
  const container = document.getElementById("tag-filters");
  if (!tags.length) {
    container.innerHTML = "";
    activeTag = "";
    return;
  }
  if (activeTag && !tags.includes(activeTag)) activeTag = "";
  container.innerHTML = tags.map(t =>
    `<button class="tag-filter-btn${activeTag === t ? " active" : ""}" data-tag="${esc(t)}"># ${esc(t)}</button>`
  ).join("");
  container.onclick = e => {
    const btn = e.target.closest(".tag-filter-btn");
    if (!btn) return;
    activeTag = activeTag === btn.dataset.tag ? "" : btn.dataset.tag;
    activeTopic = "";
    buildTagFilters();
    buildTrendingTopics();
    renderFiltered();
  };
}

function trendingTopicsForCategory(topics, articles, category) {
  if (category === "全部") return topics.slice(0, 10);
  const byId = new Map(articles.map(a => [a.id, a]));
  return topics
    .filter(t => (t.article_ids || []).some(id => byId.get(id)?.category === category))
    .slice(0, 10);
}

function buildTrendingTopics() {
  const container = document.getElementById("trending-topics");
  const topics = trendingTopicsForCategory(trendingTopics || [], all, activeCat);
  if (!topics.length) {
    container.innerHTML = "";
    activeTopic = "";
    return;
  }
  if (activeTopic && !topics.some(t => t.topic === activeTopic)) activeTopic = "";
  container.innerHTML = topics.map(t => {
    const active = activeTopic === t.topic ? " active" : "";
    const count = Number(t.count) || 0;
    const sourceCount = Number(t.source_count) || 0;
    return `<button class="trending-topic-btn${active}" data-topic="${esc(t.topic)}">
      <span>${esc(t.topic)}</span>
      <span class="trending-topic-meta">${count}篇 · ${sourceCount}源</span>
    </button>`;
  }).join("");
  container.onclick = e => {
    const btn = e.target.closest(".trending-topic-btn");
    if (!btn) return;
    activeTopic = activeTopic === btn.dataset.topic ? "" : btn.dataset.topic;
    activeTag = "";
    buildTagFilters();
    buildTrendingTopics();
    renderFiltered();
  };
}

function articleTime(article) {
  const ts = Date.parse(article.date || "");
  return Number.isFinite(ts) ? ts : 0;
}

function compareByDate(a, b) {
  return articleTime(b) - articleTime(a);
}

function aiRankScore(article, now = Date.now()) {
  const score = typeof article.score === "number" ? article.score : 5;
  const clusterSize = Math.max(1, Number(article.cluster_size) || 1);
  const clusterBonus = Math.min(clusterSize - 1, 4) * 3;
  const ageHours = (now - articleTime(article)) / 36e5;
  const recencyBonus = Number.isFinite(ageHours)
    ? Math.max(0, 6 - Math.min(Math.max(ageHours, 0), 48) / 8)
    : 0;
  return score * 10 + clusterBonus + recencyBonus;
}

function getSorted(articles) {
  if (sortMode === "score") {
    return [...articles].sort((a, b) => {
      const sa = a.score ?? 5, sb = b.score ?? 5;
      return sb !== sa ? sb - sa : compareByDate(a, b);
    });
  }
  if (sortMode === "ai") {
    const now = Date.now();
    return [...articles].sort((a, b) => {
      const delta = aiRankScore(b, now) - aiRankScore(a, now);
      return delta || compareByDate(a, b);
    });
  }
  return [...articles].sort(compareByDate);
}

function filterCluster(cid) {
  activeTag = "";
  activeTopic = "";
  const list = all.filter(a => a.cluster_id === cid);
  render(getSorted(list));
  renderMobile(getSorted(list));
}

function filteredList() {
  let list = all;
  if (searchQuery && fuse) {
    list = fuse.search(searchQuery).map(r => r.item);
  } else {
    if (activeCat !== "全部") list = list.filter(a => a.category === activeCat);
    if (activeTopic) {
      const topic = trendingTopics.find(t => t.topic === activeTopic);
      const ids = new Set((topic?.article_ids || []).map(String));
      list = list.filter(a => ids.has(String(a.id)));
    }
    if (activeTag) list = list.filter(a => (a.tags || []).includes(activeTag));
  }
  list = list.filter(a => activeSources.has(a.source));
  return getSorted(list);
}

function renderFiltered() {
  const list = filteredList();
  render(list);
  renderMobile(list);
  document.getElementById("feed-title").textContent = searchQuery ? `"${searchQuery}"` : (activeTopic || activeTag || activeCat);
  document.getElementById("feed-count").textContent = `${list.length} 篇`;
}

function renderAll() {
  renderCategories();
  renderSortButtons();
  buildSourceFilters();
  buildTagFilters();
  buildTrendingTopics();
  renderFiltered();
}

function scoreClass(score) {
  if (!score) return "score-low";
  return score >= 8 ? "score-high" : score >= 5 ? "score-mid" : "score-low";
}

function tagColor(category) {
  return {
    "新聞": "var(--tag-news)",
    "國際": "var(--tag-global)",
    "娛樂": "var(--tag-entertainment)",
    "消閒": "var(--tag-leisure)",
    "科技": "var(--tag-tech)",
    "網媒": "var(--tag-media)",
  }[category] || "var(--accent)";
}

function formatDate(article) {
  return article.date
    ? new Date(article.date).toLocaleString("zh-HK", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
    : "";
}

function summaryText(article) {
  return article.summary ? String(article.summary).replace(/\s*・/g, "\n・").trimStart() : "";
}

function thumbHtml(article, cls) {
  const thumbUrl = safeUrl(article.thumbnail);
  if (article.thumbnail && thumbUrl !== "#") {
    return `<div class="${cls}"><img src="${esc(thumbUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.parentElement.innerHTML=placeholderSvg()"></div>`;
  }
  return `<div class="${cls}">${placeholderSvg()}</div>`;
}

function placeholderSvg() {
  return `<div class="placeholder-thumb"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><path d="M4 11a9 9 0 019 9"/><path d="M4 4a16 16 0 0116 16"/><circle cx="5" cy="19" r="1"/></svg></div>`;
}
window.placeholderSvg = placeholderSvg;

function metaHtml(article, includeCluster = true) {
  const score = typeof article.score === "number" ? article.score : null;
  const scoreBadge = score !== null
    ? `<span class="important-mark">${score >= 8 ? "● 重要" : score}</span>` : "";
  const cid = /^[0-9a-f]{1,16}$/i.test(article.cluster_id || "") ? article.cluster_id : "";
  const clusterBadge = includeCluster && article.cluster_size > 1 && cid
    ? `<span class="important-mark" onclick="event.preventDefault();event.stopPropagation();filterCluster('${cid}')">${Number(article.cluster_size)}源</span>` : "";
  return `<span class="tag-pill" style="color:${tagColor(article.category)};background:color-mix(in srgb, ${tagColor(article.category)} 12%, transparent)">${esc(article.category)}</span>
    ${scoreBadge}${clusterBadge}<span class="time-label">${esc(formatDate(article))}</span>`;
}

function render(articles) {
  kbIndex = -1;
  const grid = document.getElementById("grid");
  const reads = getRead();
  if (!articles.length) {
    grid.innerHTML = '<div class="empty">沒有文章</div>';
    renderReader(null);
    return;
  }
  if (!articles.some(a => a.id === selectedId)) selectedId = articles[0]?.id || "";
  grid.innerHTML = articles.map(a => {
    const isRead = reads.has(a.id);
    const selected = a.id === selectedId;
    const aid = validArticleId(a.id) ? a.id : "";
    return `<button class="feed-item${selected ? " selected" : ""}${isRead ? " read" : ""}" data-article-id="${esc(a.id)}" data-id="${esc(aid)}" type="button">
      <div class="feed-content">
        <div class="meta-row">${metaHtml(a)}</div>
        <div class="headline">${esc(a.title)}</div>
        <div class="feed-summary">${esc(summaryText(a))}</div>
        <div class="feed-source">${esc(a.source)}</div>
      </div>
      ${thumbHtml(a, "feed-thumb")}
    </button>`;
  }).join("");
  grid.onclick = e => {
    const item = e.target.closest(".feed-item");
    if (!item) return;
    selectedId = item.dataset.articleId;
    markRead(selectedId);
    const article = all.find(a => a.id === selectedId);
    render(filteredList());
    renderMobile(filteredList());
    renderReader(article);
  };
  const selected = all.find(a => a.id === selectedId) || articles[0];
  if (isDesktop()) renderReader(selected);
}

function renderMobile(articles) {
  const list = document.getElementById("mobile-list");
  const reads = getRead();
  if (!articles.length) {
    list.innerHTML = '<div class="empty">沒有文章</div>';
    return;
  }
  const unread = articles.filter(a => !reads.has(a.id)).length;
  list.innerHTML = `<div class="unread-divider">${unread} 篇未讀</div>` + articles.map((a, i) => {
    const isRead = reads.has(a.id);
    const aid = validArticleId(a.id) ? a.id : "";
    return `<a class="mobile-card${a.score >= 8 ? " important" : ""}${isRead ? " read" : ""}" style="animation-delay:${Math.min(i, 10) * 35}ms" data-article-id="${esc(a.id)}" href="article.html?id=${encodeURIComponent(aid)}">
      <div class="mobile-card-main">
        <div class="mobile-card-content">
          <div class="meta-row">${metaHtml(a, false)}</div>
          <h3 class="headline">${esc(a.title)}</h3>
          <div class="source-line"><span>${esc(a.source)}</span><button class="share-inline" type="button" data-share="${esc(a.id)}" aria-label="分享">⌁</button></div>
        </div>
        ${thumbHtml(a, "thumb")}
      </div>
      ${isRead && summaryText(a) ? `<div class="mobile-summary">${esc(summaryText(a))}</div>` : ""}
    </a>`;
  }).join("");
  list.onclick = e => {
    const share = e.target.closest("[data-share]");
    if (share) {
      e.preventDefault();
      e.stopPropagation();
      openShare(all.find(a => a.id === share.dataset.share));
      return;
    }
    const card = e.target.closest(".mobile-card");
    if (card) markRead(card.dataset.articleId);
  };
}

async function renderReader(article) {
  const panel = document.getElementById("reading-panel");
  if (!article) {
    panel.innerHTML = `<div class="empty-panel">${placeholderSvg()}<p>點擊文章開始閱讀</p></div>`;
    return;
  }
  const aid = validArticleId(article.id) ? article.id : "";
  const lead = summaryText(article);
  panel.innerHTML = `<div class="reader-header">
    <div class="reader-meta">${metaHtml(article, false)}<span class="reader-meta-text">${esc(article.source)} · ${esc(formatDate(article))}</span></div>
    <div class="reader-actions">
      <button class="icon-btn" id="reader-share" title="分享">${shareSvg()}</button>
      <a class="icon-btn" title="開啟文章頁" href="article.html?id=${encodeURIComponent(aid)}">${openSvg()}</a>
      <button class="icon-btn" id="reader-close" title="關閉">${closeSvg()}</button>
    </div>
  </div>
  <article class="reader-scroll">
    <div class="hero-image">${article.thumbnail ? `<img src="${esc(safeUrl(article.thumbnail))}" alt="" referrerpolicy="no-referrer" onerror="this.parentElement.innerHTML=placeholderSvg()">` : placeholderSvg()}</div>
    <h1 class="reader-title">${esc(article.title)}</h1>
    ${lead ? `<p class="reader-lead">${esc(lead)}</p>` : ""}
    <div class="reader-body" id="reader-body"><p>載入內文...</p></div>
    <div class="reader-footer"><span>來源：${esc(article.source)}</span><a href="${esc(safeUrl(article.url))}" target="_blank" rel="noopener">閱讀原文 →</a></div>
  </article>`;
  document.getElementById("reader-close").onclick = () => { selectedId = ""; renderReader(null); render(filteredList()); };
  document.getElementById("reader-share").onclick = () => openShare(article);
  await loadReaderBody(article);
}

async function loadReaderBody(article) {
  const body = document.getElementById("reader-body");
  if (!body) return;
  try {
    if (!validArticleId(article.id)) throw new Error("invalid id");
    const res = await fetch(`data/content/${encodeURIComponent(article.id)}.json`);
    if (!res.ok) throw new Error("missing content");
    const data = await res.json();
    const doc = new DOMParser().parseFromString(data.content || "", "text/html");
    doc.querySelectorAll("script,style,iframe").forEach(n => n.remove());
    doc.querySelectorAll("img").forEach(img => {
      img.loading = "lazy";
      img.referrerPolicy = "no-referrer";
    });
    const html = doc.body?.innerHTML?.trim();
    body.innerHTML = html || fallbackBody(article);
  } catch {
    body.innerHTML = fallbackBody(article);
  }
}

function fallbackBody(article) {
  const text = summaryText(article) || "暫時未有全文。";
  return text.split(/\n+/).filter(Boolean).map(p => `<p>${esc(p)}</p>`).join("");
}

function shareSvg() {
  return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="18" cy="5" r="3"/><circle cx="6" cy="12" r="3"/><circle cx="18" cy="19" r="3"/><path d="M8.59 13.51l6.83 3.98"/><path d="M15.41 6.51l-6.82 3.98"/></svg>`;
}
function openSvg() {
  return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 17L17 7"/><path d="M8 7h9v9"/></svg>`;
}
function closeSvg() {
  return `<svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M18 6L6 18"/><path d="M6 6l12 12"/></svg>`;
}

function openHealthModal() {
  const body = document.getElementById("health-body");
  const entries = Object.entries(sourceStats);
  if (!entries.length) {
    body.innerHTML = '<div class="health-err">未載入來源資料</div>';
  } else {
    const sourceRank = s => s.error ? 0 : (s.count === 0 && !s.not_modified) ? 1 : s.not_modified ? 2 : 3;
    entries.sort(([, a], [, b]) => sourceRank(a) - sourceRank(b));
    body.innerHTML = entries.map(([name, s]) => {
      const effectiveCount = Number(s.effective_count ?? s.count) || 0;
      const cls = s.error ? "health-bad" : (effectiveCount === 0 && !s.not_modified) ? "health-warn" : "health-ok";
      let meta;
      if (s.error) meta = `<span class="health-err">${esc(String(s.error).slice(0, 60))}</span>`;
      else if (s.not_modified) meta = `<span class="health-meta">未更新 · 沿用</span>`;
      else meta = `<span class="health-meta">${effectiveCount} 篇${s.restored ? ` · 沿用 ${Number(s.restored) || 0}` : ""}</span>`;
      return `<div class="health-row">
        <span class="health-dot ${cls}"></span>
        <span class="health-name">${esc(name)}</span>
        <span class="health-cat">${esc(s.category || "")}</span>
        ${meta}
      </div>`;
    }).join("");
  }
  document.getElementById("health-overlay").classList.add("show");
}

function openShare(article) {
  if (!article) return;
  shareItem = article;
  const url = new URL(`article.html?id=${encodeURIComponent(article.id)}`, location.href).href;
  document.getElementById("share-body").innerHTML = `<p style="font-size:13.5px;font-weight:700;line-height:1.45;margin-bottom:14px;font-family:Georgia,serif">${esc(article.title)}</p>
    <button class="share-option" data-share-action="native">系統分享</button>
    <button class="share-option" data-share-action="copy">複製連結</button>
    <a class="share-option" style="display:block;text-decoration:none" href="${esc(url)}">開啟文章</a>`;
  document.getElementById("share-body").onclick = async e => {
    const action = e.target.closest("[data-share-action]")?.dataset.shareAction;
    if (!action) return;
    if (action === "native" && navigator.share) {
      await navigator.share({ title: shareItem.title, url }).catch(() => {});
    } else {
      await navigator.clipboard?.writeText(url).catch(() => {});
    }
    closeSheet("share-overlay");
  };
  document.getElementById("share-overlay").classList.add("show");
}

function closeSheet(id) {
  document.getElementById(id).classList.remove("show");
}

function openSearchOverlay() {
  const overlay = document.getElementById("search-overlay");
  overlay.classList.add("show");
  const input = document.getElementById("mobile-search");
  input.value = "";
  renderMobileSearch("");
  setTimeout(() => input.focus(), 50);
}
function closeSearchOverlay() {
  document.getElementById("search-overlay").classList.remove("show");
}
function renderMobileSearch(query) {
  const box = document.getElementById("search-results");
  if (!query) {
    box.innerHTML = '<div class="empty">輸入關鍵字搜尋</div>';
    return;
  }
  const results = fuse ? fuse.search(query).slice(0, 40).map(r => r.item) : [];
  if (!results.length) {
    box.innerHTML = '<div class="empty">沒有結果</div>';
    return;
  }
  box.innerHTML = results.map(a => {
    const aid = validArticleId(a.id) ? a.id : "";
    return `<a class="search-result" href="article.html?id=${encodeURIComponent(aid)}" data-article-id="${esc(a.id)}">
      <div class="meta-row">${metaHtml(a, false)}</div>
      <div class="headline" style="-webkit-line-clamp:2;font-size:14px">${esc(a.title)}</div>
      <div class="feed-source">${esc(a.source)}</div>
    </a>`;
  }).join("");
  box.onclick = e => {
    const item = e.target.closest(".search-result");
    if (item) markRead(item.dataset.articleId);
  };
}

function renderMobileSaved() {
  const reads = getRead();
  const saved = all.filter(a => reads.has(a.id));
  renderMobile(saved.length ? saved : []);
}

async function pollForNew() {
  const res = await fetch("data/articles.json?" + Date.now());
  const data = await res.json();
  sourceStats = data.sources || sourceStats;
  const newOnes = (data.articles || []).filter(a => !loadedIds.has(a.id));
  if (newOnes.length > 0) {
    pendingData = data;
    pendingNew = new Set(newOnes.map(a => a.id));
    updateHeader(data);
    showToast(newOnes.length);
  }
  return { data, newCount: newOnes.length };
}

async function checkUpdates() {
  const updEl = document.getElementById("updated");
  const mobileBtn = document.getElementById("mobile-refresh");
  if (updEl.classList.contains("busy")) return;
  updEl.classList.add("busy");
  mobileBtn.classList.add("busy");
  updEl.textContent = "檢查中";
  try {
    const { data, newCount } = await pollForNew();
    if (newCount === 0) {
      updEl.classList.add("ok");
      updEl.textContent = "已是最新";
      setTimeout(() => { updEl.classList.remove("ok"); updateHeader(data); }, 1800);
    }
  } catch {
    updEl.textContent = "檢查失敗";
  }
  updEl.classList.remove("busy");
  mobileBtn.classList.remove("busy");
}

registerServiceWorker();
load();
setInterval(async () => {
  if (document.hidden) return;
  try { await pollForNew(); } catch (_) {}
}, 10 * 60 * 1000);

document.addEventListener("keydown", e => {
  const active = document.activeElement;
  const inInput = active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA");
  if (e.key === "Escape") {
    if (document.getElementById("search-overlay").classList.contains("show")) closeSearchOverlay();
    else if (document.getElementById("health-overlay").classList.contains("show")) closeSheet("health-overlay");
    else if (inInput) { active.value = ""; searchQuery = ""; active.blur(); renderFiltered(); }
    return;
  }
  if (inInput || e.ctrlKey || e.metaKey || e.altKey) return;
  if (e.key === "/") { e.preventDefault(); document.getElementById("search").focus(); }
  else if (e.key === "j") { e.preventDefault(); focusFeed(+1); }
  else if (e.key === "k") { e.preventDefault(); focusFeed(-1); }
  else if (e.key === "g") { e.preventDefault(); document.getElementById("grid").scrollTo({ top: 0, behavior: "smooth" }); kbIndex = -1; }
  else if (e.key === "h") { e.preventDefault(); openHealthModal(); }
  else if (e.key === "Enter" && kbIndex >= 0) {
    const items = document.querySelectorAll("#grid .feed-item");
    if (items[kbIndex]) items[kbIndex].click();
  }
});

function focusFeed(delta) {
  const items = [...document.querySelectorAll("#grid .feed-item")];
  if (!items.length) return;
  kbIndex = Math.max(0, Math.min(items.length - 1, (kbIndex < 0 ? 0 : kbIndex + delta)));
  items[kbIndex].scrollIntoView({ behavior: "smooth", block: "center" });
  items[kbIndex].focus();
}
