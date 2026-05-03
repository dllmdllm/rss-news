const CATS = ["全部", ...CATEGORIES];
    let all = [], activeCat = "全部", activeSource = "", activeTag = "", sortMode = "date";
    let onlyUnread = false, onlySaved = false, onlyImportant = false;
    const IMPORTANT_SCORE_MIN = 7;
    const SENT_ICON = { positive: "▲", negative: "▼", neutral: "–" };
    let expandedClusterId = "";
    let expandedClusterSummaryId = "";
    let currentRenderArticles = [];
    let loadedIds = new Set(), pendingNew = new Set(), pendingData = null;
    let sourceStats = {};
    let panelDigests = {};       // {cluster_id: {headline, consensus, angles, tension}}
    let breakingClusters = new Set();   // cluster_ids that qualify as "breaking"
    let fuse = null;
    // Map category to CSS class; returns "" for unknown values so class
    // splitting on accidental whitespace can't happen.
    function catClass(c) { return CAT_WL.has(c) ? "cat-" + c : ""; }
    setupFontSize();
    setupThemeMode();
    setupTextOnlyMode();

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
      loadedIds = new Set(all.map(a => a.id));
      // Rebuild Fuse so search sees newly-merged articles and no longer
      // returns items that aged out of `all`.
      fuse = new Fuse(all, {
        keys: ["title", "source", "tags", "summary"],
        threshold: 0.4, minMatchCharLength: 2,
      });
        buildSourceFilters();
        buildTagFilters();
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
        const [res, digestRes] = await Promise.all([
          fetch("data/articles.json?" + Date.now()),
          fetch("data/panel_digests.json?" + Date.now()).catch(() => null),
        ]);
        const data = await res.json();
        if (digestRes && digestRes.ok) {
          try {
            const cache = await digestRes.json();
            // Cache file is keyed by cluster_id; pull `.digest` so the rendering
            // code only sees the model output, not the cache wrapper.
            panelDigests = Object.fromEntries(
              Object.entries(cache || {})
                .filter(([_, v]) => v && v.digest)
                .map(([k, v]) => [k, v.digest])
            );
          } catch (_) { panelDigests = {}; }
        }
        updateHeader(data);
        all = data.articles;
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
          const lastSuccess = h.lastSuccess ? new Date(h.lastSuccess).toLocaleString("zh-HK", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false }) : "未有紀錄";
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
    let _semCache   = {};    // query text → ordered id array
    let _semTimer   = null;
    let _semPending = null;  // query being encoded right now

    async function _runSemantic(q) {
      if (!window._semanticSearch) return;
      const idSet = new Set(all.map(a => a.id));
      const ids = await window._semanticSearch(q, idSet);
      if (ids && q === searchQuery) {
        _semCache[q] = ids;
        renderFiltered();
      }
    }

    document.addEventListener("semantic-ready", () => {
      if (searchQuery) _runSemantic(searchQuery);
    });

    document.getElementById("search").addEventListener("input", e => {
      searchQuery = e.target.value.trim();
      buildTopPicks();
      renderFiltered();
      clearTimeout(_semTimer);
      if (searchQuery && window._semanticReady) {
        _semTimer = setTimeout(() => _runSemantic(searchQuery), 300);
      }
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
      buildSourceFilters();
      buildTagFilters();
      buildTopPicks();
      renderFilteredFromUI();
      });
    }

    function syncChipFiltersVisibility() {
      const wrap     = document.getElementById("chip-filters");
      const source   = document.getElementById("source-filters");
      const tag      = document.getElementById("tag-filters");
      const divider  = document.getElementById("chip-divider");
      const hasSrc   = !!(source && source.childElementCount);
      const hasTag   = !!(tag && tag.childElementCount);
      wrap.classList.toggle("has-any", hasSrc || hasTag);
      divider.classList.toggle("show", hasSrc && hasTag);
    }

    function buildSourceFilters() {
      const container = document.getElementById("source-filters");

      if (activeCat === "全部") {
        container.innerHTML = "";
        syncChipFiltersVisibility();
        return;
      }

      const sources = [...new Set(
        all.filter(a => a.category === activeCat).map(a => a.source)
      )].sort();

      if (!sources.length) {
        container.innerHTML = "";
        syncChipFiltersVisibility();
        return;
      }

      container.innerHTML = sources.map(s =>
        `<button class="source-filter-btn${activeSource === s ? " active" : ""}" data-source="${esc(s)}">${esc(s)}</button>`
      ).join("");
      syncChipFiltersVisibility();

      container.onclick = e => {
        const btn = e.target.closest(".source-filter-btn");
        if (!btn) return;
        const src = btn.dataset.source;
        if (activeSource === src) {
          activeSource = "";
          container.querySelectorAll(".source-filter-btn").forEach(b => b.classList.remove("active"));
        } else {
          activeSource = src;
          container.querySelectorAll(".source-filter-btn").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
        }
        buildTopPicks();
        renderFilteredFromUI();
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
        syncChipFiltersVisibility();
        return;
      }
      if (activeTag && !tags.includes(activeTag)) activeTag = "";
      container.innerHTML = tags.map(t =>
        `<button class="tag-filter-btn${activeTag === t ? " active" : ""}" data-tag="${esc(t)}"># ${esc(t)}</button>`
      ).join("");
      syncChipFiltersVisibility();
      container.onclick = e => {
        const btn = e.target.closest(".tag-filter-btn");
        if (!btn) return;
        const tag = btn.dataset.tag;
        if (activeTag === tag) {
          activeTag = "";
          container.querySelectorAll(".tag-filter-btn").forEach(b => b.classList.remove("active"));
        } else {
          activeTag = tag;
          container.querySelectorAll(".tag-filter-btn").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
        }
        buildTopPicks();
        renderFilteredFromUI();
      };
    }

    const TOP_PICKS_RECENT_KEY = "topPicks.recent";
    const TOP_PICKS_RECENT_TTL_MS = 24 * 3600 * 1000;
    let _topPicksSnapshot = null;
    let _topPicksRecorded = false;

    function topPickKey(a) {
      return String(a.cluster_id || a.topic || "");
    }

    function readRecentTopPicks() {
      try {
        const obj = JSON.parse(localStorage.getItem(TOP_PICKS_RECENT_KEY) || "{}");
        const now = Date.now();
        const fresh = {};
        for (const [k, v] of Object.entries(obj)) {
          if (typeof v === "number" && now - v < TOP_PICKS_RECENT_TTL_MS) fresh[k] = v;
        }
        return fresh;
      } catch (_) { return {}; }
    }

    function recencyBoost(dateStr) {
      const ts = Date.parse(dateStr || "");
      if (isNaN(ts)) return 0;
      const hr = (Date.now() - ts) / 3600000;
      if (hr < 1) return 2;
      if (hr < 3) return 1;
      if (hr < 6) return 0.5;
      return 0;
    }

    function topPicks(articles) {
      const muted = getMutedSources();
      if (_topPicksSnapshot === null) _topPicksSnapshot = readRecentTopPicks();
      const recent = _topPicksSnapshot;
      const cats = CATS.filter(c => c !== "全部");
      const ranked = articles
        .filter(a => !muted.has(a.source))
        .filter(a => !a.duplicate_of)
        .map(a => ({ a, w: (Number(a.score) || 0) + recencyBoost(a.date) }))
        .sort((x, y) => y.w - x.w);
      const seenCluster = new Set();
      const tryPick = (pool, skipRecent) => {
        for (const { a } of pool) {
          const cid = String(a.cluster_id || "");
          if (cid && seenCluster.has(cid)) continue;
          if (skipRecent) {
            const k = topPickKey(a);
            if (k && recent[k]) continue;
          }
          if (cid) seenCluster.add(cid);
          return a;
        }
        return null;
      };
      const picks = [];
      for (const cat of cats) {
        const scope = ranked.filter(r => r.a.category === cat);
        const shortlist = scope.filter(r =>
          (Number(r.a.score) || 0) >= 7 || Number(r.a.cluster_size) > 1
        );
        const pick = tryPick(shortlist, true)
          || tryPick(shortlist, false)
          || tryPick(scope, true)
          || tryPick(scope, false);
        if (pick) picks.push(pick);
      }
      if (!_topPicksRecorded && picks.length) {
        _topPicksRecorded = true;
        const updated = { ...recent };
        const now = Date.now();
        for (const a of picks) {
          const k = topPickKey(a);
          if (k) updated[k] = now;
        }
        try { localStorage.setItem(TOP_PICKS_RECENT_KEY, JSON.stringify(updated)); } catch (_) {}
      }
      return picks;
    }

    // 0 = all, 2/4/8 = hours window
    let aiTimeFilter = 0;
    let aiEventFilter = "";  // filter by event_type, "" = all

    const AI_TIME_BTNS = [
      { label: "最新", hours: 0 },
      { label: "2小時", hours: 2 },
      { label: "4小時", hours: 4 },
      { label: "8小時", hours: 8 },
    ];

    function aiPool(maxAgeHours = 0) {
      const muted = getMutedSources();
      const now = Date.now();
      const cutoff = maxAgeHours > 0 ? now - maxAgeHours * 36e5 : 0;
      return all.filter(a => {
        if (muted.has(a.source) || a.duplicate_of) return false;
        if (cutoff > 0) {
          const ts = Date.parse(a.date || "");
          if (!Number.isFinite(ts) || ts < cutoff) return false;
        }
        return true;
      });
    }

    function topPicksByCategory(n = 10, pool) {
      const cats = CATS.filter(c => c !== "全部");
      const now = Date.now();
      const result = [];
      for (const cat of cats) {
        const ranked = pool
          .filter(a => a.category === cat)
          .map(a => ({ a, w: aiRankScore(a, now) }))
          .sort((x, y) => y.w - x.w);
        const seenCluster = new Set();
        const picks = [];
        for (const { a } of ranked) {
          if (picks.length >= n) break;
          const cid = String(a.cluster_id || "");
          if (cid && seenCluster.has(cid)) continue;
          if (cid) seenCluster.add(cid);
          picks.push(a);
        }
        if (picks.length) result.push({ cat, picks });
      }
      return result;
    }

    function _aiSentimentSection(pool) {
      const cats = CATS.filter(c => c !== "全部");
      const rows = [];
      for (const cat of cats) {
        const articles = pool.filter(a => a.category === cat);
        if (!articles.length) continue;
        const pos = articles.filter(a => a.sentiment === "positive").length;
        const neg = articles.filter(a => a.sentiment === "negative").length;
        const total = articles.length;
        const posP = Math.round(pos / total * 100);
        const negP = Math.round(neg / total * 100);
        const neuP = 100 - posP - negP;
        const neu = total - pos - neg;
        rows.push(`<div class="sent-row">
          <div class="sent-cat ${catClass(cat)}">${esc(cat)}</div>
          <div class="sent-bar">
            <div class="sent-pos" style="width:${posP}%" title="正面 ${pos}篇"></div>
            <div class="sent-neu" style="width:${neuP}%" title="中性 ${neu}篇"></div>
            <div class="sent-neg" style="width:${negP}%" title="負面 ${neg}篇"></div>
          </div>
          <div class="sent-nums">
            <span class="s-pos">正 ${pos}</span>
            <span class="s-neu">中 ${neu}</span>
            <span class="s-neg">負 ${neg}</span>
          </div>
        </div>`);
      }
      if (!rows.length) return "";
      const legend = `<span class="sent-legend"><span class="s-pos">■ 正面</span> <span class="s-neu">■ 中性</span> <span class="s-neg">■ 負面</span></span>`;
      return `<div class="ai-section">
        <div class="ai-section-hd">📊 情緒概覽 ${legend}</div>
        <div class="sentiment-grid">${rows.join("")}</div>
      </div>`;
    }

    function _aiClusterSection(pool) {
      const clusterMap = {};
      for (const a of pool) {
        const cid = String(a.cluster_id || "");
        if (!cid) continue;
        if (!clusterMap[cid]) clusterMap[cid] = { articles: [], digest: panelDigests[cid] };
        clusterMap[cid].articles.push(a);
      }
      const entries = Object.entries(clusterMap)
        .filter(([, v]) => v.digest && v.digest.headline)
        .sort(([, a], [, b]) => b.articles.length - a.articles.length)
        .slice(0, 5);
      if (!entries.length) return "";
      const cards = entries.map(([, { articles: arts, digest }]) => {
        const top = [...arts].sort((a, b) => (b.score || 0) - (a.score || 0))[0];
        const href = top ? `article.html?id=${encodeURIComponent(top.id)}` : "#";
        const sourceSentMap = {};
        for (const a of arts) if (a.source && a.sentiment) sourceSentMap[a.source] = a.sentiment;
        const anglesHtml = (digest.angles || []).map(ang => {
          const sent = (ang.sources || []).map(s => sourceSentMap[s]).find(Boolean) || "neutral";
          return `
          <div class="cd-angle" data-sent="${esc(sent)}">
            <div class="cd-angle-label">${esc(ang.label || "")}</div>
            <div class="cd-angle-sources">${(ang.sources || []).map(s => `<span>${esc(s)}</span>`).join(" · ")}</div>
            <div class="cd-angle-detail">${esc(ang.detail || "")}</div>
          </div>`;
        }).join("");
        const tensionHtml = digest.tension ? `<div class="cd-tension">⚡ ${esc(digest.tension)}</div>` : "";
        return `<div class="cluster-digest">
          <a class="cd-headline" href="${esc(href)}">${esc(digest.headline)}</a>
          <div class="cd-consensus">${esc(digest.consensus || "")}</div>
          <div class="cd-angles">${anglesHtml}</div>
          ${tensionHtml}
          <div class="cd-meta">${arts.length} 篇報道</div>
        </div>`;
      }).join("");
      return `<div class="ai-section">
        <div class="ai-section-hd">🗞️ 話題聚焦</div>
        ${cards}
      </div>`;
    }

    function _aiEventSection(pool) {
      const counts = {};
      for (const a of pool) {
        const t = (a.event_type || "").trim();
        if (t) counts[t] = (counts[t] || 0) + 1;
      }
      const sorted = Object.entries(counts).sort(([, a], [, b]) => b - a).slice(0, 14);
      if (!sorted.length) return "";
      const pills = sorted.map(([t, n]) =>
        `<span class="event-pill${aiEventFilter === t ? " active" : ""}" data-event="${esc(t)}">${esc(t)} <em>${n}</em></span>`
      ).join("");
      return `<div class="ai-section">
        <div class="ai-section-hd">📋 今日事件</div>
        <div class="event-pills">${pills}</div>
      </div>`;
    }

    function _aiTagSection(pool) {
      const counts = {};
      for (const a of pool) {
        for (const t of (a.tags || [])) {
          if (t) counts[t] = (counts[t] || 0) + 1;
        }
      }
      const sorted = Object.entries(counts).sort(([, a], [, b]) => b - a).slice(0, 24);
      if (!sorted.length) return "";
      const max = sorted[0][1];
      const tags = sorted.map(([t, n]) => {
        const sz = (0.78 + (n / max) * 0.38).toFixed(2);
        return `<span class="hot-tag" style="font-size:${sz}rem" data-tag="${esc(t)}">${esc(t)} <em>${n}</em></span>`;
      }).join("");
      return `<div class="ai-section">
        <div class="ai-section-hd">🏷️ 熱門標籤</div>
        <div class="hot-tags">${tags}</div>
      </div>`;
    }

    function buildTopPicks() {
      const container = document.getElementById("top-picks");
      if (!container) return;
      if (activeTab !== "ai" || searchQuery || activeCat !== "全部" || activeSource || activeTag || onlyUnread || onlySaved || onlyImportant) {
        container.classList.remove("show");
        container.innerHTML = "";
        return;
      }

      let pool = aiPool(aiTimeFilter);
      if (aiEventFilter) pool = pool.filter(a => (a.event_type || "").trim() === aiEventFilter);

      const groups = topPicksByCategory(10, pool);
      container.classList.add("show");

      const navHtml = `<div class="ai-nav-strip">
        <a class="ai-nav-btn" href="graph.html">🕸️ 圖譜</a>
        <a class="ai-nav-btn" href="upcoming.html">📅 預告</a>
        <a class="ai-nav-btn" href="entities.html">👤 實體</a>
      </div>`;
      const stripHtml = `<div class="ai-time-strip">${
        AI_TIME_BTNS.map(b =>
          `<button class="ai-time-btn${b.hours === aiTimeFilter ? " active" : ""}" data-hours="${b.hours}">${esc(b.label)}</button>`
        ).join("")
      }</div>`;

      const rawPool = aiPool(aiTimeFilter);
      const sentHtml    = _aiSentimentSection(rawPool);
      const clusterHtml = _aiClusterSection(rawPool);
      const eventHtml   = _aiEventSection(rawPool);
      const tagHtml     = _aiTagSection(rawPool);

      const catHtml = groups.length
        ? groups.map(({ cat, picks }) => {
            const catAttr = CAT_WL.has(cat) ? ` data-cat="${esc(cat)}"` : "";
            const rows = picks.map(a => {
              const aid = /^[0-9a-f]{1,32}$/i.test(a.id || "") ? a.id : "";
              const ago = relativeTime(a.date);
              const score = typeof a.score === "number" ? a.score : 5;
              return `<a class="ai-pick" href="article.html?id=${encodeURIComponent(aid)}"${catAttr}>
                <div class="ai-pick-title">${esc(a.title || "")}</div>
                <div class="ai-pick-meta">
                  <span class="ai-pick-source">${esc(a.source || "")}</span>
                  <span>重要度 ${score}</span>
                  ${ago ? `<span>${esc(ago)}</span>` : ""}
                </div>
              </a>`;
            }).join("");
            const heading = aiEventFilter ? `${esc(aiEventFilter)} — ${esc(cat)}` : esc(cat);
            return `<div class="ai-cat-section">
              <div class="ai-cat-label ${catClass(cat)}">${heading}</div>
              ${rows}
            </div>`;
          }).join("")
        : `<div style="color:var(--muted);font-size:.85rem;padding:16px 0">此時段暫無新聞</div>`;

      container.innerHTML = navHtml + stripHtml + sentHtml + clusterHtml + eventHtml + tagHtml
        + `<div class="ai-section-hd" style="margin-top:8px">📰 今日重點</div>` + catHtml;

      container.querySelectorAll(".ai-time-btn").forEach(btn => {
        btn.addEventListener("click", () => {
          aiTimeFilter = Number(btn.dataset.hours);
          aiEventFilter = "";
          buildTopPicks();
        });
      });
      container.querySelectorAll(".event-pill").forEach(pill => {
        pill.addEventListener("click", () => {
          const t = pill.dataset.event || "";
          aiEventFilter = aiEventFilter === t ? "" : t;
          buildTopPicks();
        });
      });
      container.querySelectorAll(".hot-tag").forEach(tag => {
        tag.addEventListener("click", () => {
          const t = tag.dataset.tag || "";
          activeTag = activeTag === t ? "" : t;
          switchTab("home");
        });
      });
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
      document.querySelectorAll(".tag-filter-btn").forEach(b => b.classList.remove("active"));
      render(getSorted(all.filter(a => a.cluster_id === cid)));
    }

    function collapseCluster() {
      expandedClusterId = "";
      renderFiltered();
    }
    window.collapseCluster = collapseCluster;

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

    function panelDigestHtml(cid) {
      const d = panelDigests[cid];
      if (!d) return "";
      const angles = (d.angles || []).map(a => `
        <li>
          <div class="panel-angle-head">
            <span class="panel-angle-label">${esc(a.label || "")}</span>
            <span class="panel-angle-sources">${(a.sources || []).map(s => esc(s)).join("、")}</span>
          </div>
          ${a.detail ? `<div class="panel-angle-detail">${esc(a.detail)}</div>` : ""}
        </li>
      `).join("");
      const tension = d.tension
        ? `<div class="panel-tension"><span class="panel-tension-label">分歧</span> ${esc(d.tension)}</div>`
        : "";
      const contradictionItems = (d.contradictions || []).map(c => `
        <li class="panel-contradiction-item">
          <span class="panel-contradiction-type">${esc(c.type || "矛盾")}</span>
          <div class="panel-contradiction-claims">
            <div><span class="panel-contradiction-source">${esc(c.source_a)}</span> ${esc(c.claim_a)}</div>
            <div class="panel-contradiction-vs">vs</div>
            <div><span class="panel-contradiction-source">${esc(c.source_b)}</span> ${esc(c.claim_b)}</div>
          </div>
        </li>`).join("");
      const contradictionsHtml = contradictionItems
        ? `<div class="panel-contradictions"><span class="panel-section-label panel-contradiction-label">⚠ 事實矛盾</span><ul class="panel-contradiction-list">${contradictionItems}</ul></div>`
        : "";
      const timelineItems = (d.timeline || []).map(t => `
        <li class="panel-timeline-item">
          <span class="panel-timeline-date">${esc(t.date || "")}</span>
          <span class="panel-timeline-event">${esc(t.event || "")}</span>
        </li>`).join("");
      const timelineHtml = timelineItems
        ? `<div class="panel-timeline"><span class="panel-section-label panel-timeline-label">時間軸</span><ul class="panel-timeline-list">${timelineItems}</ul></div>`
        : "";
      return `<div class="panel-digest">
        ${d.headline ? `<div class="panel-headline">${esc(d.headline)}</div>` : ""}
        ${d.consensus ? `<div class="panel-consensus"><span class="panel-consensus-label">共識</span> ${esc(d.consensus)}</div>` : ""}
        ${angles ? `<ul class="panel-angles">${angles}</ul>` : ""}
        ${tension}
        ${contradictionsHtml}
        ${timelineHtml}
      </div>`;
    }

    function computeBreakingClusters() {
      const TWO_HOURS_MS = 2 * 60 * 60 * 1000;
      const now = Date.now();
      const byCluster = {};
      for (const a of all) {
        if (!a.cluster_id || a.duplicate_of) continue;
        (byCluster[a.cluster_id] = byCluster[a.cluster_id] || []).push(a);
      }
      const result = new Set();
      for (const [cid, members] of Object.entries(byCluster)) {
        const recent = members.filter(a => {
          const ts = Date.parse(a.date || "");
          return !isNaN(ts) && (now - ts) <= TWO_HOURS_MS;
        });
        const sources = new Set(recent.map(a => a.source).filter(Boolean));
        if (sources.size >= 3) result.add(cid);
      }
      return result;
    }

    function sentimentTimelineHtml(cid) {
      const members = getSorted(all.filter(a => a.cluster_id === cid && !a.duplicate_of));
      if (members.length < 2) return "";
      const dots = members.map(a => {
        const sent = ["positive", "negative", "neutral"].includes(a.sentiment) ? a.sentiment : "neutral";
        const source = a.source || "";
        const time = a.date
          ? new Date(a.date).toLocaleString("zh-HK", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })
          : "";
        const tooltip = source + (time ? " · " + time : "");
        return `<span class="sent-tl-dot sent-${sent}" title="${esc(tooltip)}"></span>`;
      }).join("");
      return `<div class="sent-timeline"><span class="sent-tl-label">情緒</span>${dots}</div>`;
    }

    function clusterSummaryHtml(cid, suffix = "") {
      const articles = getSorted(all.filter(a => a.cluster_id === cid));
      if (!articles.length) return "";
      const { digestHtml, sourceRows } = aiSummaryBlockHtml(articles, "cluster");
      const idSuffix = suffix ? `-${esc(suffix)}` : "";
      return `<div class="cluster-ai-summary" id="cluster-summary-${esc(cid)}${idSuffix}">
        <div class="cluster-ai-title">AI 綜合摘要</div>
        ${sentimentTimelineHtml(cid)}
        ${panelDigestHtml(cid)}
        ${digestHtml}
        <div class="cluster-source-list">${sourceRows}</div>
      </div>`;
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
      if (searchQuery && parsedSearch.text) {
        const allowed = new Set(list.map(a => a.id));
        const semIds = _semCache[parsedSearch.text];
        if (semIds) {
          // Semantic results: reorder by similarity then append any allowed articles not in result.
          const semSet = new Set(semIds);
          const inSem  = semIds.filter(id => allowed.has(id)).map(id => all.find(a => a.id === id)).filter(Boolean);
          const notSem = list.filter(a => !semSet.has(a.id));
          list = [...inSem, ...notSem];
        } else if (fuse) {
          list = fuse.search(parsedSearch.text).map(r => r.item).filter(a => allowed.has(a.id));
        }
      } else {
        if (activeCat !== "全部") list = list.filter(a => a.category === activeCat);
        if (activeSource) list = list.filter(a => a.source === activeSource);
        if (activeTag) list = list.filter(a => (a.tags || []).includes(activeTag));
      }
      breakingClusters = computeBreakingClusters();
      let sorted = getSorted(compactClusters(list));
      if (breakingClusters.size > 0 && !searchQuery) {
        const isB = a => !!(a.cluster_id && breakingClusters.has(a.cluster_id));
        sorted = [...sorted.filter(isB), ...sorted.filter(a => !isB(a))];
      }
      render(sorted, { scrollToTop: _renderFilteredScrollTop });
      _renderFilteredScrollTop = false;
    }
    let _renderFilteredScrollTop = false;
    function renderFilteredFromUI() { _renderFilteredScrollTop = true; renderFiltered(); }

    function scoreClass(score) {
      if (!score) return "score-low";
      return score >= 8 ? "score-high" : score >= 5 ? "score-mid" : "score-low";
    }

    function render(articles, { scrollToTop = false } = {}) {
      kbIndex = -1;
      currentRenderArticles = articles;
      const grid  = document.getElementById("grid");
      const savedScrollY = scrollToTop ? 0 : window.scrollY;
      const isMobileCard = window.matchMedia?.("(max-width: 640px)")?.matches;
      const reads = getRead();
      const bookmarks = getBookmarks();
      const downranked = getDownrankSources();
      const renderedClusterSummaries = new Set();
      saveArticleNavContext(articles);
      if (!articles.length) {
        grid.innerHTML = '<div class="empty">沒有文章</div>';
        return;
      }
      grid.innerHTML = articles.map(a => {
        const date = a.date
          ? new Date(a.date).toLocaleString("zh-HK", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit", hour12: false })
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
          ? `<span class="cluster-badge" onclick="event.preventDefault();event.stopPropagation();${isExpandedCluster ? "collapseCluster()" : `filterCluster('${cid}')`}">${Number(a.cluster_size)} 來源 · ${isExpandedCluster ? "點擊收起" : "點擊展開"}</span>` : "";
        const hasContradiction = isCluster && cid && (panelDigests[cid]?.contradictions || []).length > 0;
        const contradictionBadge = hasContradiction
          ? `<span class="contradiction-badge" title="各來源有事實矛盾">⚠ 矛盾</span>` : "";
        const isBreakingCluster = isCluster && cid && breakingClusters.has(cid);
        const breakingBadge = isBreakingCluster
          ? `<span class="breaking-badge" title="突發：多媒體 2 小時內同步報導">🔴 突發</span>` : "";
        const digest = isClusterStack ? panelDigests[cid] : null;
        const digestIndicators = digest ? [
          (digest.timeline || []).length >= 2 ? "時間軸" : null,
          (digest.contradictions || []).length > 0 ? "⚠矛盾" : null,
        ].filter(Boolean).join(" · ") : null;
        const clusterSummaryButton = isClusterStack
          ? `<span class="cluster-ai-btn${expandedClusterSummaryId === cid ? " active" : ""}" role="button" tabindex="0" onclick="event.preventDefault();event.stopPropagation();toggleClusterSummary('${cid}')" onkeydown="handleClusterSummaryKey(event,'${cid}')">${expandedClusterSummaryId === cid ? "收起摘要" : "AI 綜合摘要" + (digestIndicators ? ` <span class="digest-preview">${esc(digestIndicators)}</span>` : "")}</span>`
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
        const catCls = catClass(a.category);
        const cardClass = `card ${catCls}${score !== null && score >= 8 ? " important" : ""}${isRead ? " read" : ""}${isBookmarked ? " bookmarked" : ""}${isDownranked ? " downranked-source" : ""}${isClusterStack ? " cluster-stack" : ""}${isExpandedCluster ? " cluster-expanded" : ""}${isBreakingCluster ? " breaking" : ""}`;
        const cardHref = isClusterStack ? `#cluster-${cid}` : `article.html?id=${encodeURIComponent(aid)}`;
        const cardClick = isClusterStack ? ` onclick="event.preventDefault();filterCluster('${cid}')"` : "";
        const sourceName = esc(a.source || "");
        const actionBar = `<span class="card-actions">
          <span class="mini-action${isBookmarked ? " active" : ""}" role="button" title="收藏" onclick="event.preventDefault();event.stopPropagation();toggleBookmark('${aid}')">★</span>
          <span class="mini-action" role="button" title="靜音來源" onclick="event.preventDefault();event.stopPropagation();toggleSourceMute('${sourceName}')">×</span>
          <span class="mini-action${isDownranked ? " active" : ""}" role="button" title="降權來源" onclick="event.preventDefault();event.stopPropagation();toggleSourceDownrank('${sourceName}')">↓</span>
        </span>`;
        const shouldRenderClusterSummary = isClusterStack
          && expandedClusterSummaryId === cid
          && !renderedClusterSummaries.has(cid);
        if (shouldRenderClusterSummary) renderedClusterSummaries.add(cid);
        const clusterBodySummary = shouldRenderClusterSummary
          ? clusterSummaryHtml(cid, "body")
          : "";
        return `<a class="${cardClass}" href="${cardHref}"${cardClick}>
          <div class="card-media">
            ${thumb}
          </div>
          <div class="card-body">
            <div class="card-meta">
              <span class="cat ${catCls}">${esc(a.category)}</span>
              <span class="source">${esc(a.source)}</span>
              ${scoreBadge}${sentDot}${clusterBadge}${contradictionBadge}${breakingBadge}${actionBar}
              <span class="date">${esc(date)}</span>
            </div>
            <div class="card-title ${catCls}">${esc(a.title)}</div>
            ${tags}
            ${clusterBodySummary}
            ${summaryHtml}
          </div>
        </a>`;
      }).join("");
      if (typeof window.scrollTo === "function") window.scrollTo({ top: savedScrollY, behavior: "instant" });
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

    // ── Bottom tab bar ────────────────────────────────────────────
    let activeTab = "home";
    document.body.dataset.tab = "home";

    function switchTab(tab) {
      activeTab = tab;
      document.body.dataset.tab = tab;
      document.querySelectorAll(".tab-btn").forEach(b =>
        b.classList.toggle("active", b.dataset.tab === tab)
      );

      if (tab === "home") {
        activeCat = "全部"; activeSource = ""; activeTag = ""; onlyImportant = false;
        buildTopPicks();
        renderFilteredFromUI();
        pollForNew().catch(() => null);
      } else if (tab === "ai") {
        buildTopPicks();
      } else if (tab === "hot") {
        activeCat = "全部"; activeSource = ""; activeTag = ""; onlyImportant = true;
        buildTopPicks();
        _renderFilteredScrollTop = true;
        renderFiltered();
      } else if (tab === "settings") {
        _updateSettingsPanel();
      }
    }

    function _renderHot() {
      breakingClusters = computeBreakingClusters();
      const muted = getMutedSources();
      let list = all.filter(a => !muted.has(a.source) && !a.duplicate_of);
      list = list.filter(a =>
        (Number(a.score) || 0) >= 5 ||
        (a.cluster_id && breakingClusters.has(a.cluster_id))
      );
      let sorted = getSorted(compactClusters(list));
      if (breakingClusters.size > 0) {
        const isB = a => !!(a.cluster_id && breakingClusters.has(a.cluster_id));
        sorted = [...sorted.filter(isB), ...sorted.filter(x => !isB(x))];
      }
      render(sorted, { scrollToTop: true });
    }

    function _updateSettingsPanel() {
      const isDateSort = document.querySelector('[data-sort="date"]')?.classList.contains("active");
      document.getElementById("s-sort-date")?.classList.toggle("active", !!isDateSort);
      document.getElementById("s-sort-ai")?.classList.toggle("active",   !isDateSort);
      document.getElementById("s-important")?.classList.toggle("active", onlyImportant);
      document.getElementById("s-unread")?.classList.toggle("active",    onlyUnread);
      document.getElementById("s-saved")?.classList.toggle("active",     onlySaved);
      document.getElementById("s-text")?.classList.toggle("active",      document.body.classList.contains("text-only"));
    }

    // Bind tab clicks via event delegation (more reliable than inline onclick)
    document.getElementById("tab-bar")?.addEventListener("click", e => {
      const btn = e.target.closest("[data-tab]");
      if (btn) switchTab(btn.dataset.tab);
    });

    window.switchTab           = switchTab;
    window.updateSettingsPanel = _updateSettingsPanel;
