(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

  // articleUrl, timeLabel, summaryIsTitleFallback, criticalScore live in
  // docs/js/common.js — shared with redesign.js.

  function summaryPoints(article, limit = 5) {
    if (summaryIsTitleFallback(article)) return [];
    // 唔好用 "-" 做分隔符：會炒散「5-4 裁決」「e-sports」呢類內容。
    const raw = String(article.summary || "").replace(/\\n/g, "\n").trim();
    let points = raw
      .split(/\n|・|•|●/)
      .map((line) => line.replace(/\s+/g, " ").trim())
      .filter(Boolean);
    if (points.length <= 1) {
      points = raw.split(/。|；|;/).map((line) => line.trim()).filter(Boolean);
    }
    return points.slice(0, limit);
  }

  function priorityLabel(article) {
    const score = criticalScore(article);
    if (score >= 105) return `🔥 必讀`;
    if (score >= 80) return `⭐ 推薦`;
    return `一般`;
  }

  function canonicalImageUrl(url) {
    try {
      const parsed = new URL(String(url || ""), location.href);
      parsed.hash = "";
      return parsed.href;
    } catch {
      return String(url || "").trim();
    }
  }

  // 用嚟比對 hero thumbnail 同 content 第一張圖係咪同一張。各家來源嘅
  // size variant URL 唔同寫法，依次 strip 走：
  //   1. dimension suffix「_1200x630.jpg」（TVB / 部分 CMS）
  //   2. single letter size suffix「_01s.jpg」/「_01p.jpg」（東網 on.cc）
  // 再剝走 query string 以後就可以匹配同一張原圖。
  function imageSignature(url) {
    try {
      const parsed = new URL(String(url || ""), location.href);
      let path = parsed.pathname;
      path = path.replace(/[_-]\d+x\d+(\.[a-z]+)$/i, "$1");
      path = path.replace(/(\d)[a-z](\.[a-z]+)$/i, "$1$2");
      return parsed.origin + path;
    } catch {
      return String(url || "").trim().replace(/\?.*$/, "");
    }
  }

  function sanitizeHtml(html, heroImage = "") {
    const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
    doc.querySelectorAll("script, style, iframe, object, embed, form, input, button").forEach((node) => node.remove());
    doc.querySelectorAll("*").forEach((node) => {
      [...node.attributes].forEach((attr) => {
        const name = attr.name.toLowerCase();
        const value = String(attr.value || "");
        if (name.startsWith("on") || value.trim().toLowerCase().startsWith("javascript:")) {
          node.removeAttribute(attr.name);
        }
      });
    });
    const heroSig = imageSignature(heroImage);
    const firstImage = doc.querySelector("img");
    if (firstImage && heroSig && imageSignature(firstImage.getAttribute("src")) === heroSig) {
      firstImage.remove();
    }
    doc.querySelectorAll("img").forEach((img) => {
      img.setAttribute("referrerpolicy", "no-referrer");
      img.setAttribute("loading", "lazy");
      img.setAttribute("decoding", "async");
    });
    return doc.body.innerHTML;
  }

  function stripHtml(html) {
    const doc = new DOMParser().parseFromString(String(html || ""), "text/html");
    return doc.body.textContent.replace(/\s+/g, " ").trim();
  }

  function titleKey(article) {
    return String(article.title || "").replace(/\s+/g, "").slice(0, 18);
  }

  // 排走已經喺「同來源新聞」出現嘅 id，同埋 cluster 近似標題只留一篇，
  // 避免 AI panel 同 side panel 重複顯示同一單新聞。
  function relatedArticles(current, articles, excludeIds, limit = 6, similarMap = null) {
    const seenTitles = new Set([titleKey(current)].filter(Boolean));
    const skip = excludeIds instanceof Set ? excludeIds : new Set(excludeIds || []);
    const picked = [];

    // 首選語義相似（similar.json，embed.py 用 sentence-transformers 計嘅
    // cosine top-5）。呢個檔一直生成咗但全站冇人讀（2026-07-25 review），
    // 而佢比下面嗰個 tag/topic heuristic 準——同一單新聞嘅唔同媒體版本、
    // 同一脈絡但唔同事件嘅背景報道，heuristic 兩樣都捉唔到。
    const byId = new Map(articles.map((row) => [row.id, row]));
    for (const id of (similarMap && similarMap[current.id]) || []) {
      if (picked.length >= limit) break;
      const row = byId.get(id);
      if (!row || row.id === current.id || skip.has(row.id)) continue;
      const key = titleKey(row);
      if (key && seenTitles.has(key)) continue;
      if (key) seenTitles.add(key);
      picked.push(row);
    }
    if (picked.length >= limit) return picked;

    // 補位：similar.json 可能冇呢篇（新文仲未 embed／相似度都低過 0.45）。
    const pickedIds = new Set(picked.map((row) => row.id));
    return picked.concat(articles
      .filter((article) => article.id !== current.id && !skip.has(article.id) && !pickedIds.has(article.id))
      .map((article) => {
        let score = 0;
        if (article.topic && article.topic === current.topic) score += 80;
        if (article.category === current.category) score += 18;
        if (article.source === current.source) score += 10;
        const tags = new Set(current.tags || []);
        score += (article.tags || []).filter((tag) => tags.has(tag)).length * 12;
        return { article, score };
      })
      .filter((row) => row.score > 0)
      .sort((a, b) => b.score - a.score || String(b.article.date || "").localeCompare(String(a.article.date || "")))
      .reduce((acc, row) => {
        // 個 cap 係 limit 減走語義嗰批已經佔咗嘅位。
        if (picked.length + acc.length >= limit) return acc;
        const key = titleKey(row.article);
        if (key && seenTitles.has(key)) return acc;
        if (key) seenTitles.add(key);
        acc.push(row.article);
        return acc;
      }, []));
  }

  function renderMiniArticle(article) {
    const points = summaryPoints(article, 5);
    let summary = "";
    if (points.length) {
      summary = `<ul class="mini-summary">${points.map((point) => `<li>${esc(point)}</li>`).join("")}</ul>`;
    } else if (summaryIsTitleFallback(article)) {
      summary = `<span class="summary-pending">🤖 AI 摘要稍後補上</span>`;
    }
    return `<a href="${articleUrl(article)}">
      <span>${esc(article.category || "")} · ${esc(article.source || "")} · ${esc(timeLabel(article))}</span>
      <strong>${esc(article.title || "")}</strong>
      ${summary}
    </a>`;
  }

  // 各媒體點講：panel_digests.json 以 cluster_id 做 key，內含共識／各報
  // 角度／矛盾點（build-time 由 MiniMax 生成，之前只有 index AI tab 用）。
  function renderPanelDigest(panelMap, article) {
    const host = $("panelBlock");
    if (!host) return;
    const entry = article.cluster_id && panelMap ? panelMap[article.cluster_id] : null;
    const digest = entry && entry.digest;
    if (!digest || (!digest.consensus && !(digest.angles || []).length)) {
      host.hidden = true;
      return;
    }
    const angles = (digest.angles || []).slice(0, 4).map((angle) => `
      <div class="panel-angle">
        <strong>${esc(angle.label || "")}</strong>
        <span class="panel-angle-src">${esc((angle.sources || []).join("、"))}</span>
        <p>${esc(angle.detail || "")}</p>
      </div>`).join("");
    const contradictions = (digest.contradictions || []).slice(0, 3).map((row) => `
      <li><span class="src">${esc(row.source_a || "")}</span>：${esc(row.claim_a || "")}<br>
          <span class="src">${esc(row.source_b || "")}</span>：${esc(row.claim_b || "")}</li>`).join("");
    host.hidden = false;
    host.innerHTML = `<h2>各媒體點講</h2>`
      + (digest.consensus ? `<p class="panel-consensus">${esc(digest.consensus)}</p>` : "")
      + angles
      + (contradictions ? `<h3 class="panel-sub">⚡ 各報矛盾位</h3><ul class="panel-contra">${contradictions}</ul>` : "");
  }

  // 讀完唔使撳返去再揀：內文尾提供「下一篇」——同來源、時間上緊接住
  // 呢篇嘅舊一篇；同來源冇就退而求同分類。
  function renderNextArticle(current, articles) {
    const host = $("nextArticle");
    if (!host) return;
    const pick = (pool) => pool
      .filter((a) => a.id !== current.id && String(a.date || "") < String(current.date || ""))
      .sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")))[0];
    const next = pick(articles.filter((a) => a.source === current.source))
      || pick(articles.filter((a) => a.category === current.category));
    if (!next) return;
    host.href = articleUrl(next);
    $("nextTitle").textContent = next.title || "";
    $("nextMeta").textContent = [next.source, timeLabel(next)].filter(Boolean).join(" · ");
    host.hidden = false;
  }

  function bindToolbar() {
    const buttons = [$("fontSmall"), $("fontNormal"), $("fontLarge")];
    // rss_font_size 係同 index.html 共用嘅偏好（small / normal / large）；
    // rss_home_font_size 係舊 key。冇儲過就維持本頁預設「大」。
    const FONT_KEY = "rss_font_size";
    const FONT_BUTTON_ID = { small: "fontSmall", normal: "fontNormal", large: "fontLarge" };
    function applyFontSize(size) {
      const next = FONT_BUTTON_ID[size] ? size : "large";
      document.body.classList.remove("fs-small", "fs-large");
      if (next === "small") document.body.classList.add("fs-small");
      if (next === "large") document.body.classList.add("fs-large");
      buttons.forEach((b) => b.classList.toggle("active", b.id === FONT_BUTTON_ID[next]));
      return next;
    }
    applyFontSize(localStorage.getItem(FONT_KEY) || localStorage.getItem("rss_home_font_size") || "large");
    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        const size = Object.keys(FONT_BUTTON_ID).find((key) => FONT_BUTTON_ID[key] === button.id);
        const applied = applyFontSize(size);
        try { localStorage.setItem(FONT_KEY, applied); } catch (_) {}
      });
    });
    $("textOnly").addEventListener("click", () => {
      document.body.classList.toggle("text-only");
      $("textOnly").classList.toggle("active", document.body.classList.contains("text-only"));
    });
    const aiToggle = $("aiToggle");
    if (aiToggle) {
      const applyAiState = (collapsed) => {
        document.body.classList.toggle("ai-collapsed", collapsed);
        aiToggle.classList.toggle("active", !collapsed);
      };
      applyAiState(localStorage.getItem("article.aiCollapsed") === "1");
      aiToggle.addEventListener("click", () => {
        const collapsed = !document.body.classList.contains("ai-collapsed");
        applyAiState(collapsed);
        localStorage.setItem("article.aiCollapsed", collapsed ? "1" : "0");
      });
    }
  }

  async function load() {
    bindToolbar();
    const id = new URLSearchParams(location.search).get("id");
    // cache: "no-cache" 行 ETag revalidation（304 唔使重新下載）；
    // 舊做法 ?Date.now() + no-store 每次都全量拉成個 articles.json。
    // panel_digests 係 optional enrichment——fetch 失敗唔可以拖冧成頁。
    // contentRes 都要同一個道理：之前冇 .catch()，一旦呢個 fetch
    // reject（connection 層面失敗，唔止 HTTP 錯誤），成個 Promise.all
    // 會冧晒，連本身已經攞到嘅文章標題/meta 都跌落「載入失敗」畫面——
    // 本來應該可以優雅降級做「暫時未有全文內容」（2026-07-21 audit
    // finding）。
    const [metaRes, contentRes, panelRes, similarRes] = await Promise.all([
      fetch("data/articles.json", { cache: "no-cache" }),
      fetch(`data/content/${encodeURIComponent(id)}.json`, { cache: "no-cache" }).catch(() => null),
      fetch("data/panel_digests.json", { cache: "no-cache" }).catch(() => null),
      // similar.json 同 panel_digests 一樣係 optional enrichment——fetch
      // 失敗就淨用下面嗰個 tag/topic heuristic，唔可以拖冧成頁。
      fetch("data/similar.json", { cache: "no-cache" }).catch(() => null),
    ]);
    if (!metaRes.ok) throw new Error("讀取文章列表失敗");
    const data = await metaRes.json();
    const article = (data.articles || []).find((row) => row.id === id);
    if (!article) throw new Error("搵唔到呢篇文章");
    if (contentRes && contentRes.ok) {
      const contentData = await contentRes.json();
      if (contentData && contentData.content) article.content = contentData.content;
    }

    let panelMap = null;
    if (panelRes && panelRes.ok) {
      try { panelMap = await panelRes.json(); } catch (_) {}
    }
    let similarMap = null;
    if (similarRes && similarRes.ok) {
      try { similarMap = await similarRes.json(); } catch (_) {}
    }

    document.title = `${article.title || "新聞"} · 新聞控制台`;
    $("updated").textContent = data.updated || "";
    const clickbait = Number.isInteger(article.headline_fit) && article.headline_fit <= 3
      ? `<span class="clickbait" title="AI 評估標題同內文相符度 ${article.headline_fit}/10">⚠️ 標題誇大</span>`
      : "";
    $("eyebrow").innerHTML = `
      <span class="chip">${esc(article.category || "未分類")}</span>
      <span>${esc(article.source || "")}</span>
      <span>${esc(timeLabel(article))}</span>
      <span class="priority">${esc(priorityLabel(article))}</span>${clickbait}`;
    $("title").textContent = article.title || "";
    // AI 摘要全部入 summaryBox，唔再揀一點上 dek 做副題，避免「dek 一行像
    // article lead，summaryBox 又有同一句」嘅 user confusion。Dek slot 收起。
    const summaryItems = summaryPoints(article, 5);
    const dekEl = $("dek");
    dekEl.textContent = "";
    dekEl.style.display = "none";
    $("hero").innerHTML = article.thumbnail ? `<img src="${esc(article.thumbnail)}" alt="">` : "";
    const summaryInner = summaryItems.length
      ? summaryItems.map((point) => `<li>${esc(point)}</li>`).join("")
      : `<li class="summary-pending">🤖 AI 摘要稍後補上</li>`;
    $("summaryBox").innerHTML = `<h2>AI 摘要</h2><ul>${summaryInner}</ul>`;
    $("content").innerHTML = article.content ? sanitizeHtml(article.content, article.thumbnail || "") : `<div class="error">暫時未有全文內容。</div>`;
    $("sourceLink").href = article.url || "#";

    const plain = stripHtml(article.content || "");
    const mins = Math.max(1, Math.ceil(plain.length / 550));
    $("readTime").textContent = `${mins} 分鐘閱讀 · ${plain.length || 0} 字`;

    $("metaList").innerHTML = [
      ["來源", article.source || "-"],
      ["分類", article.category || "-"],
      ["話題", article.topic || "-"],
      ["情緒", article.sentiment || "-"],
    ].map(([key, value]) => `<li><span>${esc(key)}</span><strong>${esc(value)}</strong></li>`).join("");

    const sameSourceSeenTitles = new Set([titleKey(article)].filter(Boolean));
    const sameSource = (data.articles || [])
      .filter((row) => row.id !== article.id && row.source === article.source)
      .sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")))
      .reduce((acc, row) => {
        if (acc.length >= 10) return acc;
        const key = titleKey(row);
        if (key && sameSourceSeenTitles.has(key)) return acc;
        if (key) sameSourceSeenTitles.add(key);
        acc.push(row);
        return acc;
      }, []);
    const sameSourceIds = new Set(sameSource.map((row) => row.id));
    const sourceLabel = article.source || "";
    $("sourceListTitle").textContent = sourceLabel ? `${sourceLabel} 其他新聞` : "同來源新聞";
    $("sourceList").innerHTML = sameSource.map(renderMiniArticle).join("") || `<span class="ai-note">暫時未有同來源新聞</span>`;

    const baseScore = Number(article.score || 0);
    $("priorityNote").innerHTML = `
      <div class="priority" title="優先度 ${criticalScore(article)}（AI 重要性 ${baseScore}/10 + 新鮮度 + 重複度）">${esc(priorityLabel(article))}</div>`;

    renderPanelDigest(panelMap, article);
    renderNextArticle(article, data.articles || []);

    const facts = (article.key_sentences && article.key_sentences.length)
      ? article.key_sentences
      : summaryPoints(article, 5);
    $("facts").innerHTML = facts.slice(0, 5).map((fact) => `<li>${esc(fact)}</li>`).join("");
    $("relatedList").innerHTML = relatedArticles(article, data.articles || [], sameSourceIds, 6, similarMap).map(renderMiniArticle).join("") || `<span class="ai-note">暫時未有相關新聞</span>`;
  }

  load().catch((err) => {
    $("title").textContent = "載入失敗";
    $("content").innerHTML = `<div class="error">${esc(err.message)}</div>`;
  });

  registerServiceWorker();
}());
