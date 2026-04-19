const CATS = ["全部", "新聞", "國際", "娛樂", "消閒", "科技", "網媒"];
    const _CAT_WL = new Set(["新聞", "國際", "娛樂", "消閒", "科技", "網媒"]);
    let all = [], activeCat = "全部", activeSource = "", activeTag = "", sortMode = "date";
    let loadedIds = new Set(), pendingNew = new Set(), pendingData = null;
    let sourceStats = {};
    let fuse = null;
    // Map category to CSS class; returns "" for unknown values so class
    // splitting on accidental whitespace can't happen.
    function catClass(c) { return _CAT_WL.has(c) ? "cat-" + c : ""; }
    setupFontSize();

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
    toastClose.addEventListener("click", hideToast);
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
        sourceStats = data.sources || {};
        loadedIds = new Set(all.map(a => a.id));
        fuse = new Fuse(all, {
          keys: ["title", "source", "tags", "summary"],
          threshold: 0.4, minMatchCharLength: 2,
        });
        buildFilters();
        buildSourceFilters();
        buildTagFilters();
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
        body.innerHTML = entries.map(([name, s]) => {
          const effectiveCount = Number(s.effective_count ?? s.count) || 0;
          const cls = s.error ? "health-bad" : (effectiveCount === 0 && !s.not_modified) ? "health-warn" : "health-ok";
          let meta;
          if (s.error) {
            meta = `<span class="health-err">${esc(String(s.error).slice(0, 60))}</span>`;
          } else if (s.not_modified) {
            meta = `<span class="health-meta">未更新 · 沿用上次內容</span>`;
          } else {
            meta = `<span class="health-meta">${effectiveCount} 篇${s.restored ? ` · 沿用 ${Number(s.restored) || 0}` : ""}</span>`;
          }
          return `<div class="health-row">
            <span class="health-dot ${cls}"></span>
            <span class="health-name">${esc(name)}</span>
            <span class="health-cat">${esc(s.category || "")}</span>
            ${meta}
          </div>`;
        }).join("");
      }
      healthOverlay.classList.add("show");
    }

    // ── Check for updates ─────────────────────────────────────────
    async function checkUpdates() {
      const updEl = document.getElementById("updated");
      if (updEl.classList.contains("busy")) return;
      updEl.classList.add("busy");
      updEl.textContent = "檢查中…";
      try {
        const res  = await fetch("data/articles.json?" + Date.now());
        const data = await res.json();
        sourceStats = data.sources || sourceStats;
        const newOnes = data.articles.filter(a => !loadedIds.has(a.id));
        if (newOnes.length > 0) {
          pendingData = data;
          pendingNew  = new Set(newOnes.map(a => a.id));
          updateHeader(data);
          showToast(newOnes.length);
        } else {
          updEl.classList.add("ok");
          updEl.textContent = "✓ 已是最新";
          setTimeout(() => { updEl.classList.remove("ok"); updateHeader(data); }, 2000);
        }
      } catch {
        updEl.textContent = "更新：檢查失敗";
      }
      updEl.classList.remove("busy");
    }
    // Click on the dot opens source-health modal; click on text checks for updates.
    document.getElementById("updated").addEventListener("click", e => {
      if (e.target.tagName === "SPAN" && e.target.title) {
        openHealthModal();
      } else {
        checkUpdates();
      }
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
        `<button class="filter-btn${c === "全部" ? " active" : ""}${c !== "全部" ? " cat-" + esc(c) : ""}" data-cat="${esc(c)}">${esc(c)}</button>`
      ).join("");
      container.addEventListener("click", e => {
        const btn = e.target.closest(".filter-btn");
        if (!btn) return;
        container.querySelectorAll(".filter-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        activeCat = btn.dataset.cat;
        activeSource = "";
        buildSourceFilters();
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
          container.querySelectorAll(".source-filter-btn").forEach(b => b.classList.remove("active"));
          btn.classList.add("active");
        }
        renderFiltered();
      };
    }

    function buildTagFilters() {
      const tagCounts = {};
      all.forEach(a => (a.tags || []).forEach(t => { tagCounts[t] = (tagCounts[t] || 0) + 1; }));
      const tags = Object.entries(tagCounts)
        .filter(([, c]) => c >= 2).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([t]) => t);
      const container = document.getElementById("tag-filters");
      if (!tags.length) { container.style.display = "none"; return; }
      container.style.display = "";
      container.innerHTML = tags.map(t =>
        `<button class="tag-filter-btn" data-tag="${esc(t)}"># ${esc(t)}</button>`
      ).join("");
      container.addEventListener("click", e => {
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
        renderFiltered();
      });
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

    function getSorted(articles) {
      if (sortMode === "score") {
        return [...articles].sort((a, b) => {
          const sa = a.score ?? 5, sb = b.score ?? 5;
          return sb !== sa ? sb - sa : (b.date || "") > (a.date || "") ? 1 : -1;
        });
      }
      return articles;
    }

    function filterCluster(cid) {
      activeTag = "";
      document.querySelectorAll(".tag-filter-btn").forEach(b => b.classList.remove("active"));
      render(getSorted(all.filter(a => a.cluster_id === cid)));
    }

    function renderFiltered() {
      let list = all;
      // search takes priority — override category/tag if query present
      if (searchQuery && fuse) {
        list = fuse.search(searchQuery).map(r => r.item);
      } else {
        if (activeCat !== "全部") list = list.filter(a => a.category === activeCat);
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
        const summaryHtml = a.summary
          ? `<div class="card-summary">${esc(String(a.summary).replace(/\s*・/g, '\n・').trimStart())}</div>`
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
      try {
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
      } catch (_) {}
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
    // Reset keyboard index when list re-renders
    const _origRender = render;
    render = function (articles) { kbIndex = -1; return _origRender(articles); };
