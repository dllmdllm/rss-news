    const _CAT_WL = new Set(["新聞", "國際", "娛樂", "消閒", "科技", "網媒"]);
    const _SENT_WL = new Set(["positive", "negative", "neutral"]);

    // Sanitize scraped HTML: strip scripts, event handlers, javascript: URLs.
    // Content comes from trafilatura (trusted pipeline), but source sites
    // can inject arbitrary HTML via RSS, so scrub defensively.
    function sanitizeHtml(html) {
      const doc = new DOMParser().parseFromString(String(html ?? ""), "text/html");
      doc.querySelectorAll("script, iframe, object, embed, style, link, meta, base, form").forEach(el => el.remove());
      doc.querySelectorAll("*").forEach(el => {
        for (const attr of [...el.attributes]) {
          const name = attr.name.toLowerCase();
          if (name.startsWith("on")) { el.removeAttribute(attr.name); continue; }
          if (name === "href" || name === "src" || name === "xlink:href" || name === "poster") {
            const v = attr.value;
            if (/^\s*(javascript|vbscript):/i.test(v)) {
              el.removeAttribute(attr.name);
            } else if (/^\s*data:/i.test(v) && !/^\s*data:image\//i.test(v)) {
              // data:image/... is harmless; other data: URIs (html, svg) can
              // execute scripts when clicked or loaded into iframe-like tags.
              el.removeAttribute(attr.name);
            }
          }
        }
      });
      return doc.body.innerHTML;
    }

    function applySavedTheme() {
      let theme = "dark";
      try {
        const saved = localStorage.getItem("rss_theme");
        if (saved === "light" || saved === "dark") {
          theme = saved;
        } else if (window.matchMedia?.("(prefers-color-scheme: light)").matches) {
          theme = "light";
        }
      } catch (_) {}
      document.body.classList.toggle("theme-light", theme === "light");
      document.body.classList.toggle("theme-dark", theme === "dark");
      document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "light" ? "#fafaf8" : "#0f0f13");
    }

    applySavedTheme();
    setupFontSize();
    const READ_KEY = "rss_read_ids";
    const NAV_CONTEXT_KEY = "rss_article_nav_context";
    let currentSourceUrl = "";

    function articleUrl(id) {
      return "article.html?id=" + encodeURIComponent(id);
    }

    function readNavContext(currentId, articles) {
      try {
        const raw = sessionStorage.getItem(NAV_CONTEXT_KEY);
        const ctx = raw ? JSON.parse(raw) : null;
        const ids = Array.isArray(ctx?.ids)
          ? ctx.ids.map(String).filter(Boolean)
          : [];
        if (ids.includes(currentId)) return ids;
      } catch (_) {}
      return articles.map(a => a.id).filter(Boolean);
    }

    function setNavLink(el, id) {
      if (!el) return;
      if (!id) {
        el.removeAttribute("href");
        el.classList.add("disabled");
        el.setAttribute("aria-disabled", "true");
        return;
      }
      el.href = articleUrl(id);
      el.classList.remove("disabled");
      el.removeAttribute("aria-disabled");
    }

    function setupArticleNav(currentId, articles) {
      const ids = readNavContext(currentId, articles);
      const idx = ids.indexOf(currentId);
      const prevId = idx > 0 ? ids[idx - 1] : "";
      const nextId = idx >= 0 && idx < ids.length - 1 ? ids[idx + 1] : "";
      setNavLink(document.getElementById("nav-prev"), prevId);
      setNavLink(document.getElementById("nav-next"), nextId);
    }

    function clickNav(id) {
      const link = document.getElementById(id);
      if (link && link.href && !link.classList.contains("disabled")) {
        location.href = link.href;
      }
    }

    function articleFactItems(article) {
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
      for (const [label, key] of groups) {
        const values = Array.isArray(entities[key]) ? entities[key] : [];
        for (const value of values.slice(0, 4)) {
          const text = String(value || "").trim();
          if (text) items.push({ label, value: text, cls: "" });
        }
      }
      return items.slice(0, 14);
    }

    function renderArticleFacts(article) {
      const items = articleFactItems(article);
      const el = document.getElementById("art-facts");
      if (!el || !items.length) return;
      el.innerHTML = `<div class="facts-box">
        <span class="facts-label">AI 重點</span>
        <div class="facts-grid">${items.map(item =>
          `<span class="fact-chip ${item.cls}">${esc(item.label)}：${esc(item.value)}</span>`
        ).join("")}</div>
      </div>`;
    }

    function articleTimestamp(article) {
      const ts = Date.parse(article.date || "");
      return Number.isFinite(ts) ? ts : 0;
    }

    function entityValues(article, key) {
      const values = article?.entities?.[key];
      return Array.isArray(values)
        ? values.map(v => String(v || "").trim()).filter(Boolean)
        : [];
    }

    function intersection(a, b) {
      const set = new Set(b);
      return a.filter(x => set.has(x));
    }

    function relatedReasons(current, other) {
      const reasons = [];
      if (current.cluster_id && current.cluster_id === other.cluster_id) {
        reasons.push("同一事件");
      }
      if (current.topic && current.topic === other.topic) {
        reasons.push("同話題：" + current.topic);
      }
      const groups = [
        ["人物", "people"],
        ["公司", "companies"],
        ["地點", "places"],
        ["日期", "dates"],
        ["數字", "numbers"],
      ];
      for (const [label, key] of groups) {
        const shared = intersection(entityValues(current, key), entityValues(other, key));
        if (shared.length) reasons.push("同" + label + "：" + shared[0]);
      }
      if (current.event_type && current.event_type === other.event_type) {
        reasons.push("同類型：" + current.event_type);
      }
      return reasons;
    }

    function relatedScore(current, other, now = Date.now()) {
      if (!other || other.id === current.id) return 0;
      const sameCluster = current.cluster_id && current.cluster_id === other.cluster_id;
      const sameTopic = current.topic && current.topic === other.topic;
      const peopleHits = intersection(entityValues(current, "people"), entityValues(other, "people")).length;
      const companyHits = intersection(entityValues(current, "companies"), entityValues(other, "companies")).length;
      const strongEntityHits = peopleHits + companyHits;
      if (!sameCluster && !sameTopic && strongEntityHits === 0) return 0;

      let score = 0;
      if (sameCluster) score += 100;
      if (sameTopic) score += 35;
      score += peopleHits * 22;
      score += companyHits * 18;
      for (const key of ["places", "dates", "numbers"]) {
        score += intersection(entityValues(current, key), entityValues(other, key)).length * 4;
      }
      if (current.event_type && current.event_type === other.event_type && (sameCluster || sameTopic || strongEntityHits > 0)) {
        score += 3;
      }

      const ageHours = (now - articleTimestamp(other)) / 36e5;
      if (Number.isFinite(ageHours)) score += Math.max(0, 8 - Math.min(Math.max(ageHours, 0), 72) / 9);
      return score;
    }

    function relatedArticles(current, articles, limit = 6) {
      const now = Date.now();
      return articles
        .filter(a => a && a.id !== current.id)
        .map(article => ({
          article,
          score: relatedScore(current, article, now),
          reasons: relatedReasons(current, article),
        }))
        .filter(item => item.score > 0)
        .sort((a, b) => (b.score - a.score) || (articleTimestamp(b.article) - articleTimestamp(a.article)))
        .slice(0, limit);
    }

    function renderRelatedArticles(current, articles) {
      const section = document.getElementById("related-section");
      const list = document.getElementById("related-list");
      const toggle = document.getElementById("related-toggle");
      if (!section || !list || !toggle) return;
      const rows = relatedArticles(current, articles);
      if (!rows.length) {
        section.style.display = "none";
        list.innerHTML = "";
        return;
      }
      list.innerHTML = rows.map(({ article, reasons }) => {
        const date = article.date
          ? new Date(article.date).toLocaleString("zh-HK", { month: "numeric", day: "numeric", hour: "2-digit", minute: "2-digit" })
          : "";
        const reason = reasons.slice(0, 2).join(" · ");
        return `<a class="related-card" href="${esc(articleUrl(article.id))}">
          <div class="related-meta">
            <span class="related-source">${esc(article.source || "")}</span>
            <span>${esc(date)}</span>
          </div>
          <div class="related-card-title">${esc(article.title || "")}</div>
          <div class="related-reason">${esc(reason)}</div>
        </a>`;
      }).join("");
      list.classList.add("collapsed");
      toggle.textContent = `顯示 ${rows.length} 篇`;
      toggle.setAttribute("aria-expanded", "false");
      toggle.onclick = () => {
        const collapsed = list.classList.toggle("collapsed");
        toggle.textContent = collapsed ? `顯示 ${rows.length} 篇` : "收起";
        toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
      };
      section.style.display = "";
    }

    document.addEventListener("keydown", e => {
      if (e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "ArrowLeft") {
        e.preventDefault();
        clickNav("nav-prev");
      } else if (e.key === "ArrowRight") {
        e.preventDefault();
        clickNav("nav-next");
      } else if (e.key === "Escape") {
        e.preventDefault();
        location.href = "index.html";
      }
    });

    // ── Share ─────────────────────────────────────────────────────
    document.getElementById("share-btn").addEventListener("click", async () => {
      const btn   = document.getElementById("share-btn");
      const title = document.getElementById("art-title").textContent;
      const url   = currentSourceUrl || location.href;
      try {
        if (navigator.share) {
          await navigator.share({ title, url });
        } else {
          await navigator.clipboard.writeText(url);
          btn.textContent = "✓ 已複製連結";
          btn.classList.add("copied");
          setTimeout(() => { btn.textContent = "分享"; btn.classList.remove("copied"); }, 2000);
        }
      } catch (_) {}
    });

    // ── Mark as read ──────────────────────────────────────────────
    function markRead(id) {
      try {
        const read = new Set(JSON.parse(localStorage.getItem(READ_KEY) || "[]"));
        read.add(id);
        // Keep max 500 entries to avoid bloat
        const arr = [...read].slice(-500);
        localStorage.setItem(READ_KEY, JSON.stringify(arr));
      } catch (_) {}
    }

    // ── Load article ──────────────────────────────────────────────
    async function load() {
      const id = new URLSearchParams(location.search).get("id");
      if (!id) { location.href = "index.html"; return; }

      try {
        // Fetch metadata + content in parallel. Content lives in a
        // per-article file (data/content/{id}.json) so the index payload
        // stays small.
        const [metaRes, contentRes] = await Promise.all([
          fetch("data/articles.json?" + Date.now()),
          fetch("data/content/" + encodeURIComponent(id) + ".json?" + Date.now()),
        ]);
        const data = await metaRes.json();
        const art  = data.articles.find(a => a.id === id);
        if (!art) { location.href = "index.html"; return; }
        setupArticleNav(id, data.articles);
        if (contentRes.ok) {
          try {
            const c = await contentRes.json();
            if (c && c.content) art.content = c.content;
          } catch (_) {}
        }

        // Mark as read immediately
        markRead(id);

        document.title = art.title;
        document.getElementById("topbar-source").textContent = art.source;

        const date = art.date
          ? new Date(art.date).toLocaleString("zh-HK", { year: "numeric", month: "long", day: "numeric", hour: "2-digit", minute: "2-digit" })
          : "";

        const scoreNum = typeof art.score === "number" ? art.score : null;
        let scoreHtml = "";
        if (scoreNum !== null) {
          const cls = scoreNum >= 8 ? "score-high" : scoreNum >= 5 ? "score-mid" : "score-low";
          scoreHtml = `<span class="art-score ${cls}">${scoreNum >= 8 ? "🔥 " : ""}重要度 ${scoreNum}</span>`;
        }
        const sent      = _SENT_WL.has(art.sentiment) ? art.sentiment : "neutral";
        const sentLabel = { positive: "正面", negative: "負面", neutral: "中性" }[sent];
        const sentHtml  = `<span class="art-sentiment sent-${sent}">${sentLabel}</span>`;

        const cat = _CAT_WL.has(art.category) ? art.category : "";
        const srcUrl = safeUrl(art.url);
        currentSourceUrl = srcUrl !== "#" ? srcUrl : "";
        document.getElementById("art-meta").innerHTML =
          `<span class="art-cat cat-${esc(cat)}">${esc(cat)}</span>
           <a class="art-source" href="${esc(srcUrl)}" target="_blank" rel="noopener">${esc(art.source)}</a>
           ${scoreHtml}${sentHtml}
           <span class="art-date">${esc(date)}</span>`;

        document.getElementById("art-title").textContent = art.title;

        const tagsEl = document.getElementById("art-tags");
        if (art.tags && art.tags.length) {
          tagsEl.innerHTML = art.tags.map(t => `<span class="art-tag"># ${esc(t)}</span>`).join("");
        }

        renderArticleFacts(art);

        const summaryEl = document.getElementById("art-summary");
        if (art.summary) {
          const summaryText = String(art.summary).replace(/\s*・/g, '\n・').trimStart();
          summaryEl.innerHTML = `<div class="summary-box"><span class="summary-label">AI 摘要</span>${esc(summaryText)}</div>`;
        }

        const body = document.getElementById("art-content");
        if (art.content) {
          body.innerHTML = sanitizeHtml(art.content);
          body.querySelectorAll("img").forEach(img => {
            img.loading = "lazy";
            img.referrerPolicy = "no-referrer";
            img.style.maxWidth = "100%";
            img.style.height = "auto";
            img.onerror = () => { img.style.display = "none"; };
          });
          if (art.thumbnail && body.querySelectorAll("img").length === 0) {
            const thumb = safeUrl(art.thumbnail);
            if (thumb !== "#") {
              const fi = document.createElement("img");
              fi.src = thumb;
              fi.referrerPolicy = "no-referrer";
              fi.loading = "lazy";
              fi.style.cssText = "max-width:100%;height:auto;border-radius:6px;margin-bottom:1em;display:block";
              fi.onerror = () => { fi.style.display = "none"; };
              body.insertBefore(fi, body.firstChild);
            }
          }
        } else {
          body.innerHTML = `<div class="no-content">
            <p>未能擷取全文</p>
            <a href="${esc(srcUrl)}" target="_blank" rel="noopener" class="ext-link">閱讀原文 ↗</a>
          </div>`;
        }

        document.getElementById("loading").style.display = "none";
        document.getElementById("art-body").style.display = "block";
        renderRelatedArticles(art, data.articles);
      } catch {
        document.getElementById("loading").textContent = "載入失敗";
      }
    }

    load();
    registerServiceWorker();
