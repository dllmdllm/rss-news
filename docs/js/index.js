const CATS = ["全部", "新聞", "國際", "娛樂", "消閒", "科技", "網媒"];
    const _CAT_WL = new Set(["新聞", "國際", "娛樂", "消閒", "科技", "網媒"]);
    let all = [], activeCat = "全部", activeSource = "", activeTag = "", activeTopic = "", sortMode = "date";
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

    // ── Read tracking ─────────────────────────────────────────────
    const READ_KEY = "rss_read_ids";
    function getRead() {
      try { return new Set(JSON.parse(localStorage.getItem(READ_KEY) || "[]")); }
      catch { return new Set(); }
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
        loadedIds = new Set(all.map(a => a.id));
        fuse = new Fuse(all, {
          keys: ["title", "source", "tags", "summary"],
          threshold: 0.4, minMatchCharLength: 2,
        });
        buildFilters();
        buildSourceFilters();
        buildTagFilters();
        buildTrendingTopics();
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
        const sourceRank = s => s.error ? 0 : (s.count === 0 && !s.not_modified) ? 1 : s.not_modified ? 2 : 3;
        entries.sort(([, a], [, b]) => sourceRank(a) - sourceRank(b));
        const legend = `<div class="health-legend">
          <span class="health-legend-item"><span class="health-dot health-ok"></span>新抓取</span>
          <span class="health-legend-item"><span class="health-dot health-cache"></span>沿用 cache（HTTP 304）</span>
          <span class="health-legend-item"><span class="health-dot health-warn"></span>空 · 無 cache</span>
          <span class="health-legend-item"><span class="health-dot health-bad"></span>抓取失敗</span>
        </div>`;
        body.innerHTML = legend + entries.map(([name, s]) => {
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
          return `<div class="health-row">
            <span class="health-dot ${cls}" title="${esc(tip)}"></span>
            <span class="health-name">${esc(name)}</span>
            <span class="health-cat">${esc(s.category || "")}</span>
            ${meta}
          </div>`;
        }).join("");
      }
      healthOverlay.classList.add("show");
    }

    // ── Check for updates ─────────────────────────────────────────
    async function pollForNew() {
      const res  = await fetch("data/articles.json?" + Date.now());
      const data = await res.json();
      sourceStats = data.sources || sourceStats;
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
      renderFiltered();
    });

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
        renderFiltered();
      };
    }

    // ── Sort toggle ───────────────────────────────────────────────
    document.getElementById("sort-toggle").addEventListener("click", e => {
      const btn = e.target.closest(".sort-btn");
      if (!btn) return;
      document.querySelectorAll(".sort-btn").forEach(b => b.classList.remove("active"));
      btn.classList.add("active");
      sortMode = btn.dataset.sort;
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
      return articles;
    }

    function filterCluster(cid) {
      activeTag = "";
      activeTopic = "";
      document.querySelectorAll(".tag-filter-btn").forEach(b => b.classList.remove("active"));
      render(getSorted(all.filter(a => a.cluster_id === cid)));
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

    function renderFiltered() {
      let list = all;
      // search takes priority — override category/tag if query present
      if (searchQuery && fuse) {
        list = fuse.search(searchQuery).map(r => r.item);
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
      render(getSorted(list));
    }

    function scoreClass(score) {
      if (!score) return "score-low";
      return score >= 8 ? "score-high" : score >= 5 ? "score-mid" : "score-low";
    }

    function render(articles) {
      kbIndex = -1;
      const grid  = document.getElementById("grid");
      const reads = getRead();
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
          ? `<img class="card-thumb" src="${esc(thumbUrl)}" alt="" loading="lazy" onerror="this.outerHTML='<div class=\\'card-thumb-placeholder\\'>📰</div>'">`
          : `<div class="card-thumb-placeholder">📰</div>`;

        const score = (typeof a.score === "number") ? a.score : null;
        const scoreBadge = score !== null
          ? `<span class="score-badge ${scoreClass(score)}">${score >= 8 ? "🔥 " : ""}${score}</span>` : "";
        const sentiment = ["positive", "negative", "neutral"].includes(a.sentiment) ? a.sentiment : "neutral";
        const sentDot   = `<span class="sentiment sent-${sentiment}"></span>`;
        // cluster_id is 8-hex MD5; still clamp to [0-9a-f] to be defensive
        const cid = /^[0-9a-f]{1,16}$/i.test(a.cluster_id || "") ? a.cluster_id : "";
        const clusterBadge = (a.cluster_size > 1 && cid)
          ? `<span class="cluster-badge" onclick="event.preventDefault();filterCluster('${cid}')">${Number(a.cluster_size)} 來源</span>` : "";
        const tags = (a.tags || []).length
          ? `<div class="card-tags">${a.tags.map(t => `<span class="tag-chip">${esc(t)}</span>`).join("")}</div>` : "";
        const isRead = reads.has(a.id);
        const aid = /^[0-9a-f]{1,32}$/i.test(a.id || "") ? a.id : "";
        const points = summaryPoints(a.summary);
        const summaryHtml = points.length
          ? `<div class="card-summary">${points.map(p => `<div class="card-summary-line">${esc(p)}</div>`).join("")}</div>`
          : "";

        const catCls = catClass(a.category);
        return `<a class="card ${catCls}${score !== null && score >= 8 ? " important" : ""}${isRead ? " read" : ""}" href="article.html?id=${encodeURIComponent(aid)}">
          ${thumb}
          <div class="card-body">
            <div class="card-meta">
              <span class="cat ${catCls}">${esc(a.category)}</span>
              <span class="source">${esc(a.source)}</span>
              ${scoreBadge}${sentDot}${clusterBadge}
              <span class="date">${esc(date)}</span>
            </div>
            <div class="card-title ${catCls}">${esc(a.title)}</div>
            ${tags}
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
      else if (e.key === "Enter" && kbIndex >= 0) {
        const cards = document.querySelectorAll("#grid .card");
        if (cards[kbIndex]) cards[kbIndex].click();
      }
    });
