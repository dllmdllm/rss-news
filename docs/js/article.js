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
      .split(/\n|\s+(?=[・•●])/)
      .map((line) => line.replace(/^\s*[・•●]\s*/, "").replace(/\s+/g, " ").trim())
      .filter(Boolean);
    if (points.length <= 1) {
      points = (points[0] || "").split(/。|；|;/).map((line) => line.trim()).filter(Boolean);
    }
    return points.slice(0, limit);
  }

  function priorityLabel(article) {
    const score = criticalScore(article);
    if (score >= 105) return `🔥 必讀`;
    if (score >= 80) return `⭐ 推薦`;
    return `一般`;
  }

  // 每篇文都有 sentiment，但之前淨係喺右欄 metaList 打一行英文「negative」。
  // 手機睇唔到右欄，即係等於冇——放埋落 eyebrow（一般／推薦／必讀 tag 後面）
  // 兩邊都見到。
  const SENTIMENT_META = {
    positive: { emoji: "🙂", label: "正面" },
    neutral:  { emoji: "😐", label: "中性" },
    negative: { emoji: "😟", label: "負面" },
  };

  function sentimentChip(article) {
    const key = article && article.sentiment;
    const meta = SENTIMENT_META[key];
    if (!meta) return "";
    return `<span class="sentiment sent-${key}" title="AI 判斷嘅報道情緒">${meta.emoji} ${meta.label}</span>`;
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
    const allowedTags = new Set(["P", "DIV", "SPAN", "BR", "HR", "STRONG", "B", "EM", "I", "U", "S", "BLOCKQUOTE", "UL", "OL", "LI", "H1", "H2", "H3", "H4", "H5", "H6", "FIGURE", "FIGCAPTION", "IMG", "A", "TABLE", "CAPTION", "THEAD", "TBODY", "TR", "TH", "TD", "PRE", "CODE", "SUP", "SUB"]);
    doc.querySelectorAll("script, style, iframe, object, embed, form, input, button, svg, math, template").forEach(node => node.remove());
    doc.body.querySelectorAll("*").forEach(node => {
      if (!allowedTags.has(node.tagName)) { node.replaceWith(...node.childNodes); return; }
      [...node.attributes].forEach(attr => {
        const name = attr.name.toLowerCase();
        const permitted = name === "title" || name === "alt" ||
          (node.tagName === "A" && name === "href") ||
          (node.tagName === "IMG" && name === "src") ||
          (['TD', 'TH'].includes(node.tagName) && ['rowspan', 'colspan'].includes(name) && /^\d+$/.test(attr.value) && Number(attr.value) >= 1 && Number(attr.value) <= 100);
        if (!permitted) { node.removeAttribute(attr.name); return; }
        if (name === "href" || name === "src") {
          try {
            const url = new URL(attr.value, document.baseURI);
            const protocols = name === "href" ? ["https:", "http:", "mailto:"] : ["https:", "http:"];
            if (!protocols.includes(url.protocol)) node.removeAttribute(attr.name);
          } catch (_) { node.removeAttribute(attr.name); }
        }
      });
    });
    const heroSig = imageSignature(heroImage);
    const firstImage = doc.querySelector("img");
    const beforeImage = firstImage ? document.createRange() : null;
    if (beforeImage) { beforeImage.selectNodeContents(doc.body); beforeImage.setEndBefore(firstImage); }
    if (firstImage && !beforeImage.toString().trim() && heroSig && imageSignature(firstImage.getAttribute("src")) === heroSig) {
      firstImage.remove();
    }
    doc.querySelectorAll("img").forEach((img) => {
      if (!img.getAttribute("alt")) {
        const caption = img.closest("figure")?.querySelector("figcaption")?.textContent.trim();
        img.setAttribute("alt", caption || "報道配圖（來源未提供圖片描述）");
      }
      img.setAttribute("referrerpolicy", "no-referrer");
      img.setAttribute("loading", "lazy");
      img.setAttribute("decoding", "async");
    });
    return doc.body.innerHTML;
  }

  function showContentAvailability(article) {
    const fallback = article.content_quality?.fallback || "";
    const partial = fallback.startsWith("rss-") || fallback === "minimal" || !article.content;
    const notice = $("contentNotice");
    const retained = article.content_retention;
    notice.hidden = !partial && !retained;
    if (article.content_capture) {
      notice.hidden = false;
      notice.textContent = '正文來自已核對嘅公開原文快照；未代表最新自動抓取已成功更新。';
    }
    if (retained) {
      const when = retained.scraped_at ? `（擷取時間：${retained.scraped_at}）` : "（擷取時間未有記錄）";
      const status = retained.latest_fallback === "rss-blocked" ? "來源限制存取" : "最新嘗試未取得全文";
      notice.textContent = `保留上次成功擷取嘅全文${when}。${status}，現時顯示已儲存內容。`;
    }
    if (partial) {
      if (fallback.startsWith("rss-")) {
        const short = Number(article.content_quality?.chars || 0) < 150;
        notice.textContent = short
          ? "RSS 摘要：未取得全文。以下只係來源 RSS 提供嘅短摘要。"
          : "RSS 內容：未取得網頁全文，內容可能不完整。";
        if (article.content_quality?.origin === 'wordpress-public-posts') notice.textContent = '公開 WordPress API 提供嘅正文；文章網頁未能讀取，完整性未獨立核實。';
        if (!article.thumbnail && !$("content").querySelector("img")) notice.textContent += " 來源 RSS 未提供可用圖片。";
      } else {
        notice.textContent = "未取得全文：暫時未有可用正文，請查看原文。";
      }
    }
    const omitted = Number(article.content_quality?.omitted_invalid_images || 0);
    if (Number.isInteger(omitted) && omitted > 0) {
      if (notice.hidden) notice.textContent = '';
      notice.hidden = false;
      notice.textContent += `${notice.textContent ? ' ' : ''}來源有 ${omitted} 張圖片網址無效，已略過；文字及其他圖片保留。`;
    }
    document.querySelector('#articleTtsMode option[value="full"]').textContent = partial ? "現有內容" : "全文";
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
    const preferences = window.RssReaderState || {preference: storageGet, setPreference: storageSet};
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
      buttons.forEach((b) => {
        const active = b.id === FONT_BUTTON_ID[next];
        b.classList.toggle("active", active);
        b.setAttribute("aria-pressed", String(active));
      });
      return next;
    }
    applyFontSize(preferences.preference(FONT_KEY) || preferences.preference("rss_home_font_size") || "large");
    buttons.forEach((button) => {
      button.addEventListener("click", () => {
        const size = Object.keys(FONT_BUTTON_ID).find((key) => FONT_BUTTON_ID[key] === button.id);
        const applied = applyFontSize(size);
        preferences.setPreference(FONT_KEY, applied);
      });
    });
    const textOnly = preferences.preference("rss_text_only") === "1";
    document.body.classList.toggle("text-only", textOnly);
    $("textOnly").setAttribute("aria-pressed", String(textOnly));
    $("textOnly").classList.toggle("active", textOnly);
    $("textOnly").addEventListener("click", () => {
      document.body.classList.toggle("text-only");
      const active = document.body.classList.contains("text-only");
      $("textOnly").classList.toggle("active", active);
      $("textOnly").setAttribute("aria-pressed", String(active));
      preferences.setPreference("rss_text_only", active ? "1" : "0");
    });
    const aiToggle = $("aiToggle");
    if (aiToggle) {
      const applyAiState = (collapsed) => {
        document.body.classList.toggle("ai-collapsed", collapsed);
        aiToggle.classList.toggle("active", !collapsed);
        aiToggle.setAttribute("aria-expanded", String(!collapsed));
      };
      applyAiState(preferences.preference("article.aiCollapsed") === "1");
      aiToggle.addEventListener("click", () => {
        const collapsed = !document.body.classList.contains("ai-collapsed");
        applyAiState(collapsed);
        preferences.setPreference("article.aiCollapsed", collapsed ? "1" : "0");
      });
    }
  }

  async function readJson(url, milliseconds = 8000) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), milliseconds);
    try {
      const response = await fetch(url, {cache: 'no-cache', signal: controller.signal});
      return response.ok ? await response.json() : null;
    } catch (_) { return null; }
    finally { clearTimeout(timer); }
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
    const data = await readJson('data/articles.json');
    if (!data) throw new Error("讀取文章列表失敗");
    const article = (data.articles || []).find((row) => row.id === id);
    if (!article) throw new Error("搵唔到呢篇文章");
    // Metadata is useful even while the independently bounded body is pending.
    $('title').textContent = article.title || '';
    $('summaryBox').textContent = summaryPoints(article, 5).join('；');
    const hash = article.content_hash;
    const contentData = await readJson(`data/content/${encodeURIComponent(id)}.json?content_hash=${encodeURIComponent(hash || 'unknown')}`);
    // Never join a cached body from another generation to current metadata.
    article.content = null;
    if (contentData?.version === 1 && typeof hash === 'string' && /^[a-f0-9]{64}$/.test(hash)
        && contentData.content_hash === hash && typeof contentData.content === 'string') {
      try {
        const bytes = new TextEncoder().encode(contentData.content);
        const actual = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
        if (actual === hash) {
          article.content = contentData.content;
          if (contentData.quality) article.content_quality = contentData.quality;
        }
      } catch (_) { /* Verification unavailable: keep metadata and no body. */ }
    }
    let panelMap = null, similarMap = null;

    document.title = `${article.title || "新聞"} · 新聞控制台`;
    $("updated").textContent = data.updated || "";
    const clickbait = Number.isInteger(article.headline_fit) && article.headline_fit <= 3
      ? `<span class="clickbait" title="AI 評估標題同內文相符度 ${article.headline_fit}/10">⚠️ 標題誇大</span>`
      : "";
    $("eyebrow").innerHTML = `
      <span class="chip">${esc(article.category || "未分類")}</span>
      <span>${esc(article.source || "")}</span>
      <span>${esc(timeLabel(article))}</span>
      <span class="priority" title="優先度 ${criticalScore(article)}（AI 重要性 ${Number(article.score || 0)}/10 + 新鮮度 + 重複度）">${esc(priorityLabel(article))}</span>${sentimentChip(article)}${clickbait}`;
    $("title").textContent = article.title || "";
    // AI 摘要全部入 summaryBox，唔再揀一點上 dek 做副題，避免「dek 一行像
    // article lead，summaryBox 又有同一句」嘅 user confusion。Dek slot 收起。
    const summaryItems = summaryPoints(article, 5);
    const dekEl = $("dek");
    dekEl.textContent = "";
    dekEl.style.display = "none";
    $("hero").innerHTML = article.thumbnail ? `<img src="${esc(article.thumbnail)}" alt="${esc((article.title || "新聞") + "配圖")}">` : "";
    const summaryInner = summaryItems.length
      ? summaryItems.map((point) => `<li>${esc(point)}</li>`).join("")
      : `<li class="summary-pending">🤖 AI 摘要稍後補上</li>`;
    $("summaryBox").innerHTML = `<h2>AI 摘要</h2><ul>${summaryInner}</ul>`;
    $("content").innerHTML = article.content ? sanitizeHtml(article.content, article.thumbnail || "") : `<div class="error">正文暫不可用或版本不符，請稍後重新載入；可繼續查看摘要及原文。</div>`;
    showContentAvailability(article);
    window.RssArticleReading.init(article);
    const sourceUrl = safeArticleUrl(article.url);
    if (sourceUrl) $('sourceLink').href = sourceUrl;
    else { $('sourceLink').removeAttribute('href'); $('sourceLink').setAttribute('aria-disabled', 'true'); }

    const plain = stripHtml(article.content || "");
    const mins = Math.max(1, Math.ceil(plain.length / 550));
    $("readTime").textContent = `${mins} 分鐘閱讀 · ${plain.length || 0} 字`;

    $("metaList").innerHTML = [
      ["來源", article.source || "-"],
      ["分類", article.category || "-"],
      ["話題", article.topic || "-"],
      // 同 eyebrow 嗰粒 chip 用同一組字，唔好一邊「負面」一邊 raw「negative」
      ["情緒", (SENTIMENT_META[article.sentiment] || {}).label || article.sentiment || "-"],
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

    renderPanelDigest(panelMap, article);
    renderNextArticle(article, data.articles || []);

    const normalise = value => String(value || '').replace(/\s+/g, ' ').trim();
    const body = normalise(stripHtml(article.content || ''));
    const facts = (article.key_sentences || []).filter(q => typeof q === 'string' && normalise(q).length >= 8 && body.includes(normalise(q)));
    const factItems = facts.slice(0, 5).filter((fact) => String(fact || "").trim());
    $("facts").innerHTML = factItems.map((fact) => `<li>${esc(fact)}</li>`).join("");
    // 冇關鍵句就唔好留個空框喺 AI 摘要同正文之間。
    $("keyFacts").hidden = !factItems.length;
    $("relatedList").innerHTML = relatedArticles(article, data.articles || [], sameSourceIds, 6, similarMap).map(renderMiniArticle).join("") || `<span class="ai-note">暫時未有相關新聞</span>`;
    window.RssArticleEvidence?.init(article);
    // Enrichment cannot gate primary rendering, and parsing/body reads share
    // the deadline. Rejection, invalid JSON and stalls leave heuristics intact.
    readJson('data/panel_digests.json').then(value => renderPanelDigest(value, article));
    readJson('data/similar.json').then(value => {
      if (value) $('relatedList').innerHTML = relatedArticles(article, data.articles || [], sameSourceIds, 6, value).map(renderMiniArticle).join('') || `<span class="ai-note">暫時未有相關新聞</span>`;
    });
  }

  load().catch((err) => {
    $("title").textContent = "載入失敗";
    $("content").innerHTML = `<div class="error">${esc(err.message)}</div>`;
  });

  registerServiceWorker();
}());
