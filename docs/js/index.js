const CATS = ["全部", "新聞", "國際", "娛樂", "消閒", "科技", "網媒"];
    const _CAT_WL = new Set(["新聞", "國際", "娛樂", "消閒", "科技", "網媒"]);
    let all = [], activeCat = "全部", activeSource = "", activeTag = "", activeTopic = "", sortMode = "date";
    let onlyUnread = false, onlySaved = false, onlyImportant = false;
    const IMPORTANT_SCORE_MIN = 7;
    const SENT_ICON = { positive: "▲", negative: "▼", neutral: "–" };
    let expandedClusterId = "";
    let expandedClusterSummaryId = "";
    let currentRenderArticles = [];
    let loadedIds = new Set(), pendingNew = new Set(), pendingData = null;
    let trendingTopics = [];
    let sourceStats = {};
    let fuse = null;
    // Map category to CSS class; returns "" for unknown values so class
    // splitting on accidental whitespace can't happen.
    function catClass(c) { return _CAT_WL.has(c) ? "cat-" + c : ""; }
    setupFontSize();

    // ── Theme mode ────────────────────────────────────────────────
    const THEME_KEY = "rss_theme";
    function setupThemeMode() {
      const btn = document.getElementById("theme-toggle");
      if (!btn) return;

      function preferredTheme() {
        const saved = localStorage.getItem(THEME_KEY);
        if (saved === "light" || saved === "dark") return saved;
        return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
      }

      function icon(theme) {
        if (theme === "light") {
          return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
            <path d="M21 12.8A8.5 8.5 0 1111.2 3 6.5 6.5 0 0021 12.8z"/>
          </svg>`;
        }
        return `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round">
          <circle cx="12" cy="12" r="4"/>
          <path d="M12 2v2M12 20v2M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M2 12h2M20 12h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41"/>
        </svg>`;
      }

      function apply(theme) {
        document.body.classList.toggle("theme-light", theme === "light");
        document.body.classList.toggle("theme-dark", theme === "dark");
        document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#fafaf8" : "#0f0f13");
        btn.innerHTML = icon(theme);
        btn.dataset.theme = theme;
      }

      apply(preferredTheme());
      btn.addEventListener("click", () => {
        const next = btn.dataset.theme === "light" ? "dark" : "light";
        localStorage.setItem(THEME_KEY, next);
        apply(next);
      });
    }
    setupThemeMode();

    function setupQuickToggles() {
      const unreadBtn = document.getElementById("unread-toggle");
      const savedBtn = document.getElementById("saved-toggle");
      const compactBtn = document.getElementById("compact-toggle");
      const importantBtn = document.getElementById("important-toggle");

      function syncButtons() {
        unreadBtn?.classList.toggle("active", onlyUnread);
        savedBtn?.classList.toggle("active", onlySaved);
        importantBtn?.classList.toggle("active", onlyImportant);
        const compact = localStorage.getItem(COMPACT_KEY) === "1";
        compactBtn?.classList.toggle("active", compact);
        document.body.classList.toggle("view-compact", compact);
      }

      importantBtn?.addEventListener("click", () => {
        onlyImportant = !onlyImportant;
        syncButtons();
        renderFiltered();
      });
      unreadBtn?.addEventListener("click", () => {
        onlyUnread = !onlyUnread;
        if (onlyUnread) onlySaved = false;
        syncButtons();
        renderFiltered();
      });
      savedBtn?.addEventListener("click", () => {
        onlySaved = !onlySaved;
        if (onlySaved) onlyUnread = false;
        syncButtons();
        renderFiltered();
      });
      compactBtn?.addEventListener("click", () => {
        const next = localStorage.getItem(COMPACT_KEY) === "1" ? "0" : "1";
        localStorage.setItem(COMPACT_KEY, next);
        syncButtons();
      });
      syncButtons();
      // Expose so keyboard shortcuts can use the same code path.
      window.__toggleImportant = () => importantBtn?.click();
      window.__toggleUnread = () => unreadBtn?.click();
      window.__toggleSaved = () => savedBtn?.click();
    }

    // ── Read tracking ─────────────────────────────────────────────
    const READ_KEY = "rss_read_ids";
    const BOOKMARK_KEY = "rss_bookmark_ids";
    const MUTED_SOURCES_KEY = "rss_muted_sources";
    const DOWNRANK_SOURCES_KEY = "rss_downrank_sources";
    const COMPACT_KEY = "rss_compact_view";
    const SOURCE_HEALTH_KEY = "rss_source_health";
    const NAV_CONTEXT_KEY = "rss_article_nav_context";
    function getRead() {
      return readJsonSet(READ_KEY);
    }
    function getBookmarks() { return readJsonSet(BOOKMARK_KEY); }
    function getMutedSources() { return readJsonSet(MUTED_SOURCES_KEY); }
    function getDownrankSources() { return readJsonSet(DOWNRANK_SOURCES_KEY); }

    function toggleStoredSet(key, value) {
      const set = readJsonSet(key);
      if (set.has(value)) set.delete(value);
      else set.add(value);
      writeJsonSet(key, set);
      return set.has(value);
    }
    setupQuickToggles();

    function saveArticleNavContext(articles) {
      try {
        const ids = articles.map(a => a.id).filter(Boolean);
        sessionStorage.setItem(NAV_CONTEXT_KEY, JSON.stringify({
          ids,
          savedAt: Date.now(),
        }));
      } catch (_) {}
    }

    // ── Toast ─────────────────────────────────────────────────────
    const toast        = document.getElementById("news-toast");
    const toastMsg     = document.getElementById("toast-msg");
    const toastRefresh = document.getElementById("toast-refresh");
    const toastClose   = document.getElementById("toast-close");

    function showToast(count) {
      toastMsg.textContent = `↑ ${count} 篇新文章`;
      toast.classList.add("show");
    }
    function hideToast() { toast.classList.remove("show"); }
    toastClose.addEventListener("click", () => {
      // Treat dismissal as "seen" so the next poll does not re-notify for
      // the same batch the user just waved off.
      pendingNew.forEach(id => loadedIds.add(id));
      pendingNew.clear();
      pendingData = null;
      hideToast();
    });
    toastRefresh.addEventListener("click", () => { hideToast(); applyPending(); });

    function applyPending() {
      const snap = new Set(pendingNew);
      pendingNew.clear();
      all = pendingData.articles;
      trendingTopics = pendingData.trending_topics || [];
      loadedIds = new Set(all.map(a => a.id));
      // Rebuild Fuse so search sees newly-merged articles and no longer
      // returns items that aged out of `all`.
      fuse = new Fuse(all, {
        keys: ["title", "source", "tags", "summary"],
        threshold: 0.4, minMatchCharLength: 2,
      });
        buildSourceFilters();
        buildTagFilters();
        buildTrendingTopics();
        buildTopPicks();
        renderFiltered();
      setTimeout(() => {
        snap.forEach(id => {
          const card = document.querySelector(`a.card[href="article.html?id=${id}"]`);
          if (card) card.classList.add("card-new");
        });
      }, 80);
    }

    // ── Load ──────────────────────────────────────────────────────
    async function load() {
      try {
        const res  = await fetch("data/articles.json?" + Date.now());
        const data = await res.json();
        updateHeader(data);
        all = data.articles;
        trendingTopics = data.trending_topics || [];
        sourceStats = data.sources || {};
        updateSourceHealthHistory(sourceStats);
        loadedIds = new Set(all.map(a => a.id));
        fuse = new Fuse(all, {
          keys: ["title", "source", "tags", "summary"],
          threshold: 0.4, minMatchCharLength: 2,
        });
        buildFilters();
        buildSourceFilters();
        buildTagFilters();
        buildTrendingTopics();
        buildTopPicks();
        renderFiltered();
      } catch {
        document.getElementById("grid").innerHTML = '<div class="empty">載入失敗，請重試</div>';
      }
    }

    // Header shows an orange dot if any source failed this build; click opens modal.
    function updateHeader(data) {
      const updEl = document.getElementById("updated");
      const stats = data.sources || {};
      const failed = Object.values(stats).filter(s => s.error).length;
      const dot = failed ? ` <span title="${esc(failed + " 個來源失敗")}" style="color:#fbbf24">●</span>` : "";
      updEl.innerHTML = "更新：" + esc(data.updated) + dot;
    }

    // ── Source health modal ──────────────────────────────────────
    function updateSourceHealthHistory(stats) {
      try {
        const now = new Date().toISOString();
        const prev = JSON.parse(localStorage.getItem(SOURCE_HEALTH_KEY) || "{}");
        for (const [name, s] of Object.entries(stats || {})) {
          const old = prev[name] || {};
          if (s.error) {
            prev[name] = { ...old, failStreak: (Number(old.failStreak) || 0) + 1, lastError: String(s.error).slice(0, 120) };
          } else {
            prev[name] = { ...old, failStreak: 0, lastSuccess: now, lastError: "" };
          }
        }
        localStorage.setItem(SOURCE_HEALTH_KEY, JSON.stringify(prev));
      } catch (_) {}
    }

    function sourceHealthHistory() {
      try { return JSON.parse(localStorage.getItem(SOURCE_HEALTH_KEY) || "{}"); }
      catch (_) { return {}; }
    }

    const healthOverlay = document.getElementById("health-overlay");
    document.getElementById("health-close").addEventListener("click", () => healthOverlay.classList.remove("show"));
    healthOverlay.addEventListener("click", e => {
      if (e.target === healthOverlay) healthOverlay.classList.remove("show");
    });

    function openHealthModal() {
      const body = document.getElementById("health-body");
      const entries = Object.entries(sourceStats);
      if (!entries.length) {
        body.innerHTML = '<div class="health-err">未載入來源資料</div>';
      } else {
        const history = sourceHealthHistory();
        const muted = getMutedSources();
        const downranked = getDownrankSources();
        const sourceRank = s => s.error ? 0 : (s.count === 0 && !s.not_modified) ? 1 : s.not_modified ? 2 : 3;
        entries.sort(([, a], [, b]) => sourceRank(a) - sourceRank(b));
        const legend = `<div class="health-legend">
          <span class="health-legend-item"><span class="health-dot health-ok"></span>新抓取</span>
          <span class="health-legend-item"><span class="health-dot health-cache"></span>沿用 cache（HTTP 304）</span>
          <span class="health-legend-item"><span class="health-dot health-warn"></span>空 · 無 cache</span>
          <span class="health-legend-item"><span class="health-dot health-bad"></span>抓取失敗</span>
        </div>`;
        body.innerHTML = legend + entries.map(([name, s]) => {
          const h = history[name] || {};
          const effectiveCount = Number(s.effective_count ?? s.count) || 0;
          let cls, tip;
          if (s.error) {
            cls = "health-bad";
            tip = "抓取失敗";
          } else if (effectiveCount === 0 && !s.not_modified) {
            cls = "health-warn";
            tip = "今次抓唔到，亦冇 cache 可用";
          } else if (s.not_modified) {
            cls = "health-cache";
            tip = "來源回 HTTP 304（feed 未變），沿用上次文章";
          } else {
            cls = "health-ok";
            tip = "今次有新抓取內容";
          }
          let meta;
          if (s.error) {
            meta = `<span class="health-err">${esc(String(s.error).slice(0, 60))}</span>`;
          } else if (s.not_modified) {
            meta = `<span class="health-meta">未更新 · 沿用上次內容</span>`;
          } else {
            meta = `<span class="health-meta">${effectiveCount} 篇${s.restored ? ` · 沿用 ${Number(s.restored) || 0}` : ""}</span>`;
          }
          const lastSuccess = h.lastSuccess ? new Date(h.lastSuccess).toLocaleString("zh-HK", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "未有紀錄";
          const controls = `<span class="health-actions">
            <button class="health-action${muted.has(name) ? " active" : ""}" data-source-action="mute" data-source="${esc(name)}">靜音</button>
            <button class="health-action${downranked.has(name) ? " active" : ""}" data-source-action="downrank" data-source="${esc(name)}">降權</button>
          </span>`;
          const histMeta = `<span class="health-history">上次成功 ${esc(lastSuccess)}${Number(h.failStreak) ? ` · 連敗 ${Number(h.failStreak)}` : ""}</span>`;
          return `<div class="health-row">
            <span class="health-dot ${cls}" title="${esc(tip)}"></span>
            <span class="health-name">${esc(name)}</span>
            <span class="health-cat">${esc(s.category || "")}</span>
            ${meta}${histMeta}${controls}
          </div>`;
        }).join("");
      }
      healthOverlay.classList.add("show");
    }

    document.getElementById("health-body").addEventListener("click", e => {
      const btn = e.target.closest("[data-source-action]");
      if (!btn) return;
      const source = btn.dataset.source || "";
      if (!source) return;
      if (btn.dataset.sourceAction === "mute") toggleStoredSet(MUTED_SOURCES_KEY, source);
      if (btn.dataset.sourceAction === "downrank") toggleStoredSet(DOWNRANK_SOURCES_KEY, source);
      openHealthModal();
      renderFiltered();
    });

    // ── Check for updates ─────────────────────────────────────────
    async function pollForNew() {
      const res  = await fetch("data/articles.json?" + Date.now());
      const data = await res.json();
      sourceStats = data.sources || sourceStats;
      updateSourceHealthHistory(sourceStats);
      const newOnes = data.articles.filter(a => !loadedIds.has(a.id));
      if (newOnes.length > 0) {
        pendingData = data;
        pendingNew  = new Set(newOnes.map(a => a.id));
        updateHeader(data);
        showToast(newOnes.length);
      }
      return { data, newCount: newOnes.length };
    }

    async function checkUpdates() {
      const updEl = document.getElementById("updated");
      if (updEl.classList.contains("busy")) return;
      updEl.classList.add("busy");
      updEl.textContent = "檢查中…";
      try {
        const { data, newCount } = await pollForNew();
        if (newCount === 0) {
          updEl.classList.add("ok");
          updEl.textContent = "✓ 已是最新";
          setTimeout(() => { updEl.classList.remove("ok"); updateHeader(data); }, 2000);
        }
      } catch {
        updEl.textContent = "更新：檢查失敗";
      }
      updEl.classList.remove("busy");
    }
    // Click the update timestamp to inspect source health.
    document.getElementById("updated").addEventListener("click", () => {
      openHealthModal();
    });

    // ── Search ────────────────────────────────────────────────────
    let searchQuery = "";
    document.getElementById("search").addEventListener("input", e => {
      searchQuery = e.target.value.trim();
      buildTopPicks();
      renderFiltered();
    });

    function parseSearchQuery(query) {
      const filters = {};
      const terms = [];
      for (const part of String(query || "").split(/\s+/).filter(Boolean)) {
        const m = part.match(/^(source|tag|cat|topic):(.+)$/i);
        const score = part.match(/^score([<>]=?)(\d+)$/i);
        if (m) filters[m[1].toLowerCase()] = m[2];
        else if (score) filters.score = { op: score[1], value: Number(score[2]) };
        else terms.push(part);
      }
      return { filters, text: terms.join(" ") };
    }

    function passScoreFilter(article, filter) {
      if (!filter) return true;
      const score = Number(article.score) || 0;
      if (filter.op === ">") return score > filter.value;
      if (filter.op === ">=") return score >= filter.value;
      if (filter.op === "<") return score < filter.value;
      if (filter.op === "<=") return score <= filter.value;
      return true;
    }

    function applySearchOperators(list, parsed) {
      const f = parsed.filters || {};
      return list.filter(a => {
        if (f.source && !String(a.source || "").includes(f.source)) return false;
        if (f.tag && !(a.tags || []).some(t => String(t).includes(f.tag))) return false;
        if (f.cat && !String(a.category || "").includes(f.cat)) return false;
        if (f.topic && !String(a.topic || "").includes(f.topic)) return false;
        return passScoreFilter(a, f.score);
      });
    }

    // ── Filters ───────────────────────────────────────────────────
    function buildFilters() {
      const container = document.getElementById("filters");
      container.innerHTML = CATS.map(c =>
        `<button class="filter-btn${c === "全部" ? " active" : ""}${c !== "全部" ? " " + catClass(c) : ""}" data-cat="${esc(c)}">${esc(c)}</button>`
      ).join("");
      container.addEventListener("click", e => {
        const btn = e.target.closest(".filter-btn");
        if (!btn) return;
        container.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        activeCat = btn.dataset.cat;
        activeSource = "";
        activeTag = "";
        activeTopic = "";
      buildSourceFilters();
      buildTagFilters();
      buildTrendingTopics();
      buildTopPicks();
      renderFiltered();
      });
    }

    function buildSourceFilters() {
      const container = document.getElementById("source-filters");
      const tagFilters = document.getElementById("tag-filters");

      if (activeCat === "全部") {
        container.classList.remove("has-sources");
        container.innerHTML = "";
        tagFilters.style.top = "135px";
        return;
      }

      const sources = [...new Set(
        all.filter(a => a.category === activeCat).map(a => a.source)
      )].sort();

      if (!sources.length) {
        container.classList.remove("has-sources");
        container.innerHTML = "";
        tagFilters.style.top = "135px";
        return;
      }

      container.innerHTML = sources.map(s =>
        `<button class="source-filter-btn${activeSource === s ? " active" : ""}" data-source="${esc(s)}">${esc(s)}</button>`
      ).join("");
      container.classList.add("has-sources");

      // Push tag-filters down to sit below source-filters
      tagFilters.style.top = (135 + container.offsetHeight) + "px";

      container.onclick = e => {
        const btn = e.target.closest(".source-filter-btn");
        if (!btn) return;
        const src = btn.dataset.source;
        if (activeSource === src) {
          activeSource = "";
          container.querySelectorAll(".source-filter-btn").forEach(b => b.classList.remove("active"));
        } else {
          activeSource = src;
          activeTopic = "";
          container.querySelectorAll(".source-filter-btn").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
        }
        buildTrendingTopics();
        buildTopPicks();
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
        container.style.display = "none";
        container.innerHTML = "";
        activeTag = "";
        return;
      }
      if (activeTag && !tags.includes(activeTag)) activeTag = "";
      container.style.display = "";
      container.innerHTML = tags.map(t =>
        `<button class="tag-filter-btn${activeTag === t ? " active" : ""}" data-tag="${esc(t)}"># ${esc(t)}</button>`
      ).join("");
      container.onclick = e => {
        const btn = e.target.closest(".tag-filter-btn");
        if (!btn) return;
        const tag = btn.dataset.tag;
        if (activeTag === tag) {
          activeTag = "";
          container.querySelectorAll(".tag-filter-btn").forEach(b => b.classList.remove("active"));
        } else {
          activeTag = tag;
          activeTopic = "";
          container.querySelectorAll(".tag-filter-btn").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
        }
        buildTrendingTopics();
        buildTopPicks();
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
      if (!container) return;

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
        const topic = btn.dataset.topic;
        activeTopic = activeTopic === topic ? "" : topic;
        activeSource = "";
        activeTag = "";
        document.querySelectorAll(".source-filter-btn,.tag-filter-btn").forEach(b => b.classList.remove("active"));
        buildTrendingTopics();
        buildTopPicks();
        renderFiltered();
      };
    }

    function topPicks(articles, limit = 6) {
      const muted = getMutedSources();
      return getSorted(articles)
        .filter(a => !muted.has(a.source))
        .filter(a => (Number(a.score) || 0) >= 7 || Number(a.cluster_size) > 1)
        .slice(0, limit);
    }

    function buildTopPicks() {
      const container = document.getElementById("top-picks");
      if (!container) return;
      if (searchQuery || activeCat !== "全部" || activeSource || activeTag || activeTopic || onlyUnread || onlySaved || onlyImportant) {
        container.classList.remove("show");
        container.innerHTML = "";
        return;
      }
      const picks = topPicks(all);
      if (!picks.length) {
        container.classList.remove("show");
        container.innerHTML = "";
        return;
      }
      container.classList.add("show");
      container.innerHTML = `<div class="top-picks-head">
        <div class="top-picks-title">今日重點</div>
        <div class="top-picks-sub">按重要度、多來源報道同時間排序</div>
      </div>
      <div class="top-picks-list">${picks.map(a => {
        const aid = /^[0-9a-f]{1,32}$/i.test(a.id || "") ? a.id : "";
        const score = typeof a.score === "number" ? a.score : 5;
        return `<a class="top-pick" href="article.html?id=${encodeURIComponent(aid)}">
          <div class="top-pick-meta"><span>${esc(a.source || "")}</span><span>重要度 ${score}</span></div>
          <div class="top-pick-title">${esc(a.title || "")}</div>
        </a>`;
      }).join("")}</div>`;
    }

    // ── Sort toggle ───────────────────────────────────────────────
    document.getElementById("sort-toggle").addEventListener("click", e => {
      const btn = e.target.closest(".sort-btn");
      if (!btn) return;
      document.querySelectorAll(".sort-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      sortMode = btn.dataset.sort;
      buildTopPicks();
      renderFiltered();
    });

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
      const sourcePenalty = (typeof getDownrankSources === "function" && getDownrankSources().has(article.source)) ? 22 : 0;
      const ageHours = (now - articleTime(article)) / 36e5;
      const recencyBonus = Number.isFinite(ageHours)
        ? Math.max(0, 6 - Math.min(Math.max(ageHours, 0), 48) / 8)
        : 0;
      return score * 10 + clusterBonus + recencyBonus - sourcePenalty;
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

    function clusterKey(article) {
      const cid = String(article.cluster_id || "");
      const size = Number(article.cluster_size) || 1;
      return (size > 1 && /^[0-9a-f]{1,16}$/i.test(cid)) ? cid : "";
    }

    function compactClusters(articles) {
      // Drop articles flagged as near-duplicates when their canonical is
      // also visible — keeps the grid focused on distinct stories.
      const visibleIds = new Set(articles.map(a => a.id));
      const afterDup = articles.filter(a => {
        const dupOf = a.duplicate_of;
        return !(dupOf && visibleIds.has(dupOf));
      });
      const picked = new Map();
      const singles = [];
      for (const article of afterDup) {
        const key = clusterKey(article);
        if (!key) {
          singles.push(article);
          continue;
        }
        const current = picked.get(key);
        if (!current) {
          picked.set(key, article);
          continue;
        }
        const ranked = getSorted([current, article]);
        picked.set(key, ranked[0]);
      }
      return [...singles, ...picked.values()];
    }

    function filterCluster(cid) {
      expandedClusterId = cid;
      expandedClusterSummaryId = "";
      activeTag = "";
      activeTopic = "";
      document.querySelectorAll(".tag-filter-btn").forEach(b => b.classList.remove("active"));
      render(getSorted(all.filter(a => a.cluster_id === cid)));
    }

    function prefetchForOffline(id) {
      try {
        const url = "data/content/" + encodeURIComponent(id) + ".json";
        fetch(url, { cache: "reload" }).catch(() => {});
      } catch (_) {}
    }

    function toggleBookmark(id) {
      const before = getBookmarks().has(id);
      toggleStoredSet(BOOKMARK_KEY, id);
      if (!before) prefetchForOffline(id);
      render(currentRenderArticles);
    }

    function toggleSourceMute(source) {
      toggleStoredSet(MUTED_SOURCES_KEY, source);
      buildTopPicks();
      renderFiltered();
    }

    function toggleSourceDownrank(source) {
      toggleStoredSet(DOWNRANK_SOURCES_KEY, source);
      buildTopPicks();
      renderFiltered();
    }
    window.toggleBookmark = toggleBookmark;
    window.toggleSourceMute = toggleSourceMute;
    window.toggleSourceDownrank = toggleSourceDownrank;

    function toggleClusterSummary(cid) {
      expandedClusterSummaryId = expandedClusterSummaryId === cid ? "" : cid;
      render(currentRenderArticles);
    }

    function handleClusterSummaryKey(event, cid) {
      if (event.key !== "Enter" && event.key !== " ") return;
      event.preventDefault();
      event.stopPropagation();
      toggleClusterSummary(cid);
    }

    function summaryPoints(summary) {
      const text = String(summary || "").replace(/\r/g, "\n").trim();
      if (!text) return [];
      return text
        .replace(/\s*・\s*/g, "\n")
        .split(/\n+/)
        .map(line => line.replace(/^・+/, "").trim())
        .filter(Boolean);
    }

    function clusterDigestItems(articles, limit = 5) {
      const seen = new Set();
      const items = [];
      for (const article of articles) {
        for (const point of summaryPoints(article.summary)) {
          const normalized = point.replace(/\s+/g, "").toLowerCase();
          if (!normalized || seen.has(normalized)) continue;
          seen.add(normalized);
          items.push(point);
          if (items.length >= limit) return items;
        }
      }
      return items;
    }

    function clusterSummaryHtml(cid) {
      const articles = getSorted(all.filter(a => a.cluster_id === cid));
      if (!articles.length) return "";
      const digest = clusterDigestItems(articles);
      const digestHtml = digest.length
        ? `<ul class="cluster-digest-list">${digest.map(point => `<li>${esc(point)}</li>`).join("")}</ul>`
        : `<div class="cluster-empty-summary">暫時未有足夠摘要</div>`;
      const sourceRows = articles.map(article => {
        const points = summaryPoints(article.summary).slice(0, 2);
        const pointsHtml = points.length
          ? `<div class="cluster-source-points">${points.map(point => `<div>${esc(point)}</div>`).join("")}</div>`
          : "";
        return `<div class="cluster-source-row">
          <div class="cluster-source-head">
            <span class="cluster-source-name">${esc(article.source || "未知來源")}</span>
            <span class="cluster-source-title">${esc(article.title || "")}</span>
          </div>
          ${pointsHtml}
        </div>`;
      }).join("");
      return `<div class="cluster-ai-summary" id="cluster-summary-${esc(cid)}">
        <div class="cluster-ai-title">AI 綜合摘要</div>
        ${digestHtml}
        <div class="cluster-source-list">${sourceRows}</div>
      </div>`;
    }

    function keyFactItems(article) {
      const items = [];
      const type = String(article.event_type || "").trim();
      if (type) items.push({ label: "類型", value: type, cls: "fact-type" });
      const entities = article.entities || {};
      const groups = [
        ["人物", "people"],
        ["公司", "companies"],
        ["地點", "places"],
        ["日期", "dates"],
        ["數字", "numbers"],
      ];
      for (const [name, key] of groups) {
        const values = Array.isArray(entities[key]) ? entities[key] : [];
        for (const value of values) {
          const text = String(value || "").trim();
          if (text) items.push({ label: name, value: text, cls: "" });
          if (items.length >= 5) return items;
        }
      }
      return items;
    }

    function keyFactsHtml(article) {
      const items = keyFactItems(article);
      if (!items.length) return "";
      return `<div class="key-facts">${items.map(item =>
        `<span class="fact-chip ${item.cls}">${esc(item.label)}：${esc(item.value)}</span>`
      ).join("")}</div>`;
    }

    function renderFiltered() {
      expandedClusterId = "";
      expandedClusterSummaryId = "";
      let list = all;
      const reads = getRead();
      const bookmarks = getBookmarks();
      const muted = getMutedSources();
      const parsedSearch = parseSearchQuery(searchQuery);
      list = list.filter(a => !muted.has(a.source));
      if (onlyUnread) list = list.filter(a => !reads.has(a.id));
      if (onlySaved) list = list.filter(a => bookmarks.has(a.id));
      if (onlyImportant) list = list.filter(a => (Number(a.score) || 0) >= IMPORTANT_SCORE_MIN);
      list = applySearchOperators(list, parsedSearch);
      // search takes priority — override category/tag if query present
      if (searchQuery && fuse) {
        if (parsedSearch.text) {
          const allowed = new Set(list.map(a => a.id));
          list = fuse.search(parsedSearch.text).map(r => r.item).filter(a => allowed.has(a.id));
        }
      } else {
        if (activeCat !== "全部") list = list.filter(a => a.category === activeCat);
        if (activeTopic) {
          const topic = trendingTopics.find(t => t.topic === activeTopic);
          const ids = new Set((topic?.article_ids || []).map(String));
          list = list.filter(a => ids.has(String(a.id)));
        }
        if (activeSource) list = list.filter(a => a.source === activeSource);
        if (activeTag) list = list.filter(a => (a.tags || []).includes(activeTag));
      }
      render(getSorted(compactClusters(list)));
    }

    function scoreClass(score) {
      if (!score) return "score-low";
      return score >= 8 ? "score-high" : score >= 5 ? "score-mid" : "score-low";
    }

    function render(articles) {
      kbIndex = -1;
      currentRenderArticles = articles;
      const grid  = document.getElementById("grid");
      const reads = getRead();
      const bookmarks = getBookmarks();
      const downranked = getDownrankSources();
      saveArticleNavContext(articles);
      if (!articles.length) {
        grid.innerHTML = '<div class="empty">沒有文章</div>';
        return;
      }
      grid.innerHTML = articles.map(a => {
        const date = a.date
          ? new Date(a.date).toLocaleString("zh-HK", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
          : "";
        const thumbUrl = safeUrl(a.thumbnail);
        const thumb = (a.thumbnail && thumbUrl !== "#")
          ? `<img class="card-thumb" src="${esc(thumbUrl)}" alt="" loading="lazy" referrerpolicy="no-referrer" onerror="this.outerHTML='<div class=\\'card-thumb-placeholder\\'>📰</div>'">`
          : `<div class="card-thumb-placeholder">📰</div>`;

        const score = (typeof a.score === "number") ? a.score : null;
        const scoreBadge = score !== null
          ? `<span class="score-badge ${scoreClass(score)}">${score >= 8 ? "🔥 " : ""}${score}</span>` : "";
        const sentiment = ["positive", "negative", "neutral"].includes(a.sentiment) ? a.sentiment : "neutral";
        const sentLabel = sentiment === "positive" ? "正面" : sentiment === "negative" ? "負面" : "中性";
        const sentDot   = `<span class="sentiment sent-${sentiment}" role="img" aria-label="情緒：${sentLabel}" title="情緒：${sentLabel}">${SENT_ICON[sentiment]}</span>`;
        // cluster_id is 8-hex MD5; still clamp to [0-9a-f] to be defensive
        const cid = /^[0-9a-f]{1,16}$/i.test(a.cluster_id || "") ? a.cluster_id : "";
        const isCluster = Number(a.cluster_size) > 1 && cid;
        const isExpandedCluster = isCluster && expandedClusterId === cid;
        const isClusterStack = isCluster && !isExpandedCluster;
        const clusterBadge = isCluster
          ? `<span class="cluster-badge" onclick="event.preventDefault();filterCluster('${cid}')">${Number(a.cluster_size)} 來源${isClusterStack ? " · 點擊展開" : ""}</span>` : "";
        const clusterSummaryButton = isClusterStack
          ? `<span class="cluster-ai-btn${expandedClusterSummaryId === cid ? " active" : ""}" role="button" tabindex="0" onclick="event.preventDefault();event.stopPropagation();toggleClusterSummary('${cid}')" onkeydown="handleClusterSummaryKey(event,'${cid}')">${expandedClusterSummaryId === cid ? "收起摘要" : "AI 綜合摘要"}</span>`
          : "";
        const tagChips = (a.tags || []).map(t => `<span class="tag-chip">${esc(t)}</span>`).join("");
        const tags = (tagChips || clusterSummaryButton)
          ? `<div class="card-tags">${tagChips}${clusterSummaryButton}</div>` : "";
        const isRead = reads.has(a.id);
        const isBookmarked = bookmarks.has(a.id);
        const isDownranked = downranked.has(a.source);
        const aid = /^[0-9a-f]{1,32}$/i.test(a.id || "") ? a.id : "";
        const points = summaryPoints(a.summary);
        const summaryHtml = points.length
          ? `<div class="card-summary">${points.map(p => `<div class="card-summary-line">${esc(p)}</div>`).join("")}</div>`
          : "";
        const factsHtml = keyFactsHtml(a);
        const clusterSummary = isClusterStack && expandedClusterSummaryId === cid ? clusterSummaryHtml(cid) : "";

        const catCls = catClass(a.category);
        const cardClass = `card ${catCls}${score !== null && score >= 8 ? " important" : ""}${isRead ? " read" : ""}${isBookmarked ? " bookmarked" : ""}${isDownranked ? " downranked-source" : ""}${isClusterStack ? " cluster-stack" : ""}${isExpandedCluster ? " cluster-expanded" : ""}`;
        const cardHref = isClusterStack ? `#cluster-${cid}` : `article.html?id=${encodeURIComponent(aid)}`;
        const cardClick = isClusterStack ? ` onclick="event.preventDefault();filterCluster('${cid}')"` : "";
        const sourceName = esc(a.source || "");
        const actionBar = `<span class="card-actions">
          <span class="mini-action${isBookmarked ? " active" : ""}" role="button" title="收藏" onclick="event.preventDefault();event.stopPropagation();toggleBookmark('${aid}')">★</span>
          <span class="mini-action" role="button" title="靜音來源" onclick="event.preventDefault();event.stopPropagation();toggleSourceMute('${sourceName}')">×</span>
          <span class="mini-action${isDownranked ? " active" : ""}" role="button" title="降權來源" onclick="event.preventDefault();event.stopPropagation();toggleSourceDownrank('${sourceName}')">↓</span>
        </span>`;
        const clusterStrip = isCluster
          ? `<div class="cluster-strip"><span>多來源報道</span><span>${Number(a.cluster_size)} 個來源</span></div>`
          : "";
        return `<a class="${cardClass}" href="${cardHref}"${cardClick}>
          <div class="card-media">
            ${thumb}
            ${isCluster ? `<div class="card-overlay">
              ${clusterStrip}
              ${isClusterStack ? clusterSummaryButton : ""}
              ${isClusterStack && expandedClusterSummaryId === cid ? clusterSummaryHtml(cid) : ""}
            </div>` : ""}
          </div>
          <div class="card-body">
            <div class="card-meta">
              <span class="cat ${catCls}">${esc(a.category)}</span>
              <span class="source">${esc(a.source)}</span>
              ${scoreBadge}${sentDot}${clusterBadge}${actionBar}
              <span class="date">${esc(date)}</span>
            </div>
            <div class="card-title ${catCls}">${esc(a.title)}</div>
            ${tags}
            ${clusterSummary}
            ${factsHtml}
            ${summaryHtml}
          </div>
        </a>`;
      }).join("");
    }
    registerServiceWorker();

    load();
    // Background poll
    setInterval(async () => {
      if (document.hidden) return;
      try { await pollForNew(); } catch (_) {}
    }, 10 * 60 * 1000);

    // ── Keyboard shortcuts ────────────────────────────────────────
    // j/k = next/previous card, g = top, / = focus search, Esc = blur/clear.
    let kbIndex = -1;
    function focusCard(delta) {
      const cards = [...document.querySelectorAll("#grid .card")];
      if (!cards.length) return;
      kbIndex = Math.max(0, Math.min(cards.length - 1, (kbIndex < 0 ? 0 : kbIndex + delta)));
      cards.forEach(c => c.classList.remove("kb-focus"));
      const target = cards[kbIndex];
      target.classList.add("kb-focus");
      target.scrollIntoView({ behavior: "smooth", block: "center" });
    }
    document.addEventListener("keydown", e => {
      const active = document.activeElement;
      const inInput = active && (active.tagName === "INPUT" || active.tagName === "TEXTAREA");
      if (e.key === "Escape") {
        const search = document.getElementById("search");
        if (inInput) { search.value = ""; searchQuery = ""; renderFiltered(); active.blur(); }
        else if (healthOverlay.classList.contains("show")) healthOverlay.classList.remove("show");
        return;
      }
      if (inInput) return;
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "/") { e.preventDefault(); document.getElementById("search").focus(); }
      else if (e.key === "j") { e.preventDefault(); focusCard(+1); }
      else if (e.key === "k") { e.preventDefault(); focusCard(-1); }
      else if (e.key === "g") { e.preventDefault(); window.scrollTo({ top: 0, behavior: "smooth" }); kbIndex = -1; }
      else if (e.key === "h") { e.preventDefault(); openHealthModal(); }
      else if (e.key === "u") { e.preventDefault(); window.__toggleUnread?.(); }
      else if (e.key === "s") { e.preventDefault(); window.__toggleSaved?.(); }
      else if (e.key === "i" || e.key === "!") { e.preventDefault(); window.__toggleImportant?.(); }
      else if (e.key === "b") {
        e.preventDefault();
        const cards = [...document.querySelectorAll("#grid .card")];
        const target = kbIndex >= 0 ? cards[kbIndex] : null;
        if (!target) return;
        const href = target.getAttribute("href") || "";
        const match = href.match(/id=([0-9a-f]{1,32})/i);
        if (match) toggleBookmark(match[1]);
      }
      else if (e.key === "Enter" && kbIndex >= 0) {
        const cards = document.querySelectorAll("#grid .card");
        if (cards[kbIndex]) cards[kbIndex].click();
      }
    });
