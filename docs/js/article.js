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
    const raw = String(article.summary || "").trim();
    let points = raw
      .split(/\n|・|•|●|-/)
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

  function relatedArticles(current, articles, limit = 6) {
    return articles
      .filter((article) => article.id !== current.id)
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
      .slice(0, limit)
      .map((row) => row.article);
  }

  function renderMiniArticle(article) {
    const points = summaryPoints(article, 3);
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

  function bindToolbar() {
    const buttons = [$("fontSmall"), $("fontNormal"), $("fontLarge")];
    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        document.body.classList.remove("fs-small", "fs-large");
        if (button.id === "fontSmall") document.body.classList.add("fs-small");
        if (button.id === "fontLarge") document.body.classList.add("fs-large");
        buttons.forEach((b) => b.classList.toggle("active", b === button));
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
    const ts = Date.now();
    const [metaRes, contentRes] = await Promise.all([
      fetch(`data/articles.json?${ts}`, { cache: "no-store" }),
      fetch(`data/content/${encodeURIComponent(id)}.json?${ts}`, { cache: "no-store" }),
    ]);
    if (!metaRes.ok) throw new Error("讀取文章列表失敗");
    const data = await metaRes.json();
    const article = (data.articles || []).find((row) => row.id === id);
    if (!article) throw new Error("搵唔到呢篇文章");
    if (contentRes.ok) {
      const contentData = await contentRes.json();
      if (contentData && contentData.content) article.content = contentData.content;
    }

    document.title = `${article.title || "新聞"} · 新聞控制台`;
    $("updated").textContent = data.updated || "";
    $("eyebrow").innerHTML = `
      <span class="chip">${esc(article.category || "未分類")}</span>
      <span>${esc(article.source || "")}</span>
      <span>${esc(timeLabel(article))}</span>
      <span class="priority">${esc(priorityLabel(article))}</span>`;
    $("title").textContent = article.title || "";
    // 揀第一個唔係 body 開頭 verbatim prefix 嘅 summary point 做 dek，避
    // 免「dek 一行 + body 第一句」一字不差出現兩次。揀好之後 summaryBox
    // 就跳過呢點，唔好喺同一頁面再 echo 一次。
    const bodyOpening = stripHtml(article.content || "").slice(0, 60);
    const summaryItems = summaryPoints(article, 5);
    let dekPoint = "";
    for (const point of summaryItems) {
      if (!point) continue;
      if (bodyOpening && bodyOpening.startsWith(point)) continue;
      dekPoint = point;
      break;
    }
    $("dek").textContent = dekPoint;
    $("dek").classList.toggle("summary-pending", !dekPoint && summaryIsTitleFallback(article));
    if (!dekPoint && summaryIsTitleFallback(article)) $("dek").textContent = "🤖 AI 摘要稍後補上";
    $("hero").innerHTML = article.thumbnail ? `<img src="${esc(article.thumbnail)}" alt="">` : "";
    const remainingSummary = summaryItems.filter((point) => point && point !== dekPoint);
    const summaryInner = remainingSummary.length
      ? remainingSummary.map((point) => `<li>${esc(point)}</li>`).join("")
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

    const sameSource = (data.articles || [])
      .filter((row) => row.id !== article.id && row.source === article.source)
      .sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")))
      .slice(0, 10);
    const sourceLabel = article.source || "";
    $("sourceListTitle").textContent = sourceLabel ? `${sourceLabel} 其他新聞` : "同來源新聞";
    $("sourceList").innerHTML = sameSource.map(renderMiniArticle).join("") || `<span class="ai-note">暫時未有同來源新聞</span>`;

    const baseScore = Number(article.score || 0);
    $("priorityNote").innerHTML = `
      <div class="priority" title="優先度 ${criticalScore(article)}（AI 重要性 ${baseScore}/10 + 新鮮度 + 重複度）">${esc(priorityLabel(article))}</div>`;

    const facts = (article.key_sentences && article.key_sentences.length)
      ? article.key_sentences
      : summaryPoints(article, 5);
    $("facts").innerHTML = facts.slice(0, 5).map((fact) => `<li>${esc(fact)}</li>`).join("");
    $("relatedList").innerHTML = relatedArticles(article, data.articles || [], 6).map(renderMiniArticle).join("") || `<span class="ai-note">暫時未有相關新聞</span>`;
  }

  load().catch((err) => {
    $("title").textContent = "載入失敗";
    $("content").innerHTML = `<div class="error">${esc(err.message)}</div>`;
  });
}());
