(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[c]));

  function articleUrl(article) {
    return `article.html?id=${encodeURIComponent(article.id)}`;
  }

  function timeLabel(article) {
    const date = new Date(article.date || "");
    if (Number.isNaN(date.getTime())) return "";
    const diff = Date.now() - date.getTime();
    const mins = Math.max(0, Math.round(diff / 60000));
    if (mins < 60) return `${mins} 分鐘前`;
    const hours = Math.round(mins / 60);
    if (hours < 24) return `${hours} 小時前`;
    return date.toLocaleDateString("zh-HK", { month: "numeric", day: "numeric" });
  }

  function summaryIsTitleFallback(article) {
    if (!article || !article.summary || !article.title) return false;
    const norm = (s) => String(s).replace(/^・/, "").replace(/\s+/g, "").trim();
    const s = norm(article.summary);
    const t = norm(article.title);
    if (!s || !t) return false;
    return s === t || (s.length >= 12 && (t.startsWith(s) || s.startsWith(t)));
  }

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

  function criticalScore(article) {
    const score = Number(article.score || 0);
    const title = `${article.title || ""} ${article.summary || ""}`;
    const event = String(article.event_type || "");
    let boost = 0;
    if (/突發|事故|火警|爆炸|拘捕|詐騙|死亡|襲擊|制裁|戰爭|地震|疫情|法庭|判刑/.test(title)) boost += 18;
    if (/刑事|事故|政治|法庭|衛生|國際/.test(event)) boost += 10;
    if (article.cluster_size > 1) boost += Math.min(18, article.cluster_size * 2);
    const date = new Date(article.date || "");
    const ageHours = Number.isNaN(date.getTime()) ? 24 : Math.max(0, (Date.now() - date.getTime()) / 3600000);
    const freshness = Math.max(0, 18 - ageHours * 1.4);
    return Math.round(score * 8 + boost + freshness);
  }

  function priorityLabel(article) {
    return `優先度 ${criticalScore(article)}`;
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
    const heroUrl = canonicalImageUrl(heroImage);
    const firstImage = doc.querySelector("img");
    if (firstImage && heroUrl && canonicalImageUrl(firstImage.getAttribute("src")) === heroUrl) {
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
    const dekPoint = summaryPoints(article, 1)[0] || "";
    $("dek").textContent = dekPoint;
    $("dek").classList.toggle("summary-pending", !dekPoint && summaryIsTitleFallback(article));
    if (!dekPoint && summaryIsTitleFallback(article)) $("dek").textContent = "🤖 AI 摘要稍後補上";
    $("hero").innerHTML = article.thumbnail ? `<img src="${esc(article.thumbnail)}" alt="">` : "";
    const summaryItems = summaryPoints(article, 5);
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

    const sameSource = (data.articles || [])
      .filter((row) => row.id !== article.id && row.source === article.source)
      .sort((a, b) => String(b.date || "").localeCompare(String(a.date || "")))
      .slice(0, 5);
    $("sourceList").innerHTML = sameSource.map(renderMiniArticle).join("") || `<span class="ai-note">暫時未有同來源新聞</span>`;

    const baseScore = Number(article.score || 0);
    $("priorityNote").innerHTML = `
      <div class="priority">${esc(priorityLabel(article))}</div>
      <p class="ai-note">由 AI 重要性 ${baseScore}/10、新聞新鮮度、事件類型同同話題重複度組合而成。</p>`;

    const facts = (article.key_sentences && article.key_sentences.length)
      ? article.key_sentences
      : summaryPoints(article, 5);
    $("facts").innerHTML = facts.slice(0, 6).map((fact) => `<li>${esc(fact)}</li>`).join("");
    $("relatedList").innerHTML = relatedArticles(article, data.articles || [], 6).map(renderMiniArticle).join("") || `<span class="ai-note">暫時未有相關新聞</span>`;
  }

  load().catch((err) => {
    $("title").textContent = "載入失敗";
    $("content").innerHTML = `<div class="error">${esc(err.message)}</div>`;
  });
}());
