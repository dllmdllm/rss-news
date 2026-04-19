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
    setupFontSize();
    const READ_KEY = "rss_read_ids";

    // ── Share ─────────────────────────────────────────────────────
    document.getElementById("share-btn").addEventListener("click", async () => {
      const btn   = document.getElementById("share-btn");
      const title = document.getElementById("art-title").textContent;
      const url   = location.href;
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
      } catch {
        document.getElementById("loading").textContent = "載入失敗";
      }
    }

    load();
    registerServiceWorker();
