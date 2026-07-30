const CATEGORIES = ["新聞", "國際", "外媒", "娛樂", "消閒", "科技", "網媒"];
const CAT_WL = new Set(CATEGORIES);

const _ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => _ESC[c]);
}

function safeUrl(u) {
  const s = String(u ?? "").trim();
  return /^https?:\/\//i.test(s) ? s : "#";
}

// localStorage access itself (not just writes) can throw SecurityError under
// blocked-cookies/private-mode. setupThemeMode/setupTextOnlyMode/setupFontSize
// run at page-init time on entities.html/upcoming.html/graph.html — an
// unguarded getItem() there used to be able to crash init before any UI
// wired up, the same failure class fixed in index.js's storageGet()
// (2026-07-21 audit finding).
function storageGet(key, fallback = null) {
  try { return localStorage.getItem(key) ?? fallback; } catch (_) { return fallback; }
}
function storageSet(key, value) {
  try { localStorage.setItem(key, value); } catch (_) {}
}

function readJsonSet(key) {
  try {
    const arr = JSON.parse(localStorage.getItem(key) || "[]");
    return new Set(Array.isArray(arr) ? arr.map(String) : []);
  } catch (_) {
    return new Set();
  }
}

function writeJsonSet(key, set, limit = 1000) {
  try {
    localStorage.setItem(key, JSON.stringify([...set].slice(-limit)));
  } catch (_) {}
}

const THEME_KEY = "rss_theme";
const TEXT_ONLY_KEY = "rss_text_only";

function _applyTheme(theme) {
  document.body.classList.toggle("theme-light", theme === "light");
  document.body.classList.toggle("theme-dark", theme === "dark");
  document.querySelector('meta[name="theme-color"]')
    ?.setAttribute("content", theme === "light" ? "#fafaf8" : "#0f0f13");
}

function _themeIcon(theme) {
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

function setupThemeMode() {
  const saved = storageGet(THEME_KEY);
  let theme = (saved === "light" || saved === "dark")
    ? saved
    : (window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark");
  _applyTheme(theme);
  const btn = document.getElementById("theme-toggle");
  if (!btn) return;
  btn.innerHTML = _themeIcon(theme);
  btn.dataset.theme = theme;
  btn.addEventListener("click", () => {
    theme = theme === "light" ? "dark" : "light";
    storageSet(THEME_KEY, theme);
    _applyTheme(theme);
    btn.innerHTML = _themeIcon(theme);
    btn.dataset.theme = theme;
  });
}

function setupTextOnlyMode() {
  const enabled = storageGet(TEXT_ONLY_KEY) === "1";
  document.body.classList.toggle("text-only", enabled);
  const btn = document.getElementById("text-toggle");
  if (!btn) return;
  function syncBtn(on) {
    btn.textContent = on ? "圖" : "文";
    btn.title = on ? "顯示圖片" : "切換純文字模式";
    btn.dataset.textOnly = on ? "1" : "0";
  }
  syncBtn(enabled);
  btn.addEventListener("click", () => {
    const next = btn.dataset.textOnly !== "1";
    document.body.classList.toggle("text-only", next);
    storageSet(TEXT_ONLY_KEY, next ? "1" : "0");
    syncBtn(next);
  });
}

function setupFontSize() {
  let fsLevel = parseInt(storageGet("fontSize", "1"));
  if (isNaN(fsLevel) || fsLevel < 0 || fsLevel > 2) fsLevel = 1;

  function applyFs() {
    document.body.className = document.body.className.replace(/\bfs-\d\b/g, "").trim();
    document.body.classList.add("fs-" + fsLevel);
    document.getElementById("font-dec").classList.toggle("disabled", fsLevel === 0);
    document.getElementById("font-inc").classList.toggle("disabled", fsLevel === 2);
  }

  applyFs();
  document.getElementById("font-inc").addEventListener("click", () => {
    if (fsLevel < 2) {
      fsLevel++;
      storageSet("fontSize", fsLevel);
      applyFs();
    }
  });
  document.getElementById("font-dec").addEventListener("click", () => {
    if (fsLevel > 0) {
      fsLevel--;
      storageSet("fontSize", fsLevel);
      applyFs();
    }
  });
}

// Render a date as a relative time like "15 小時前" / "23 分鐘前".
// Returns "" when the input cannot be parsed.
function relativeTime(dateStr) {
  const ts = Date.parse(dateStr || "");
  if (isNaN(ts)) return "";
  const diffSec = Math.max(0, (Date.now() - ts) / 1000);
  if (diffSec < 60) return "剛剛";
  const min = Math.round(diffSec / 60);
  if (min < 60) return `${min} 分鐘前`;
  const hr = Math.round(diffSec / 3600);
  if (hr < 48) return `${hr} 小時前`;
  const day = Math.round(diffSec / 86400);
  return `${day} 日前`;
}

// Split an AI summary string into its individual bullet points.
// Handles both newline-separated bullets and "・" delimited single-line output.
function summaryPoints(summary) {
  const text = String(summary || "").replace(/\\n/g, "\n").replace(/\r/g, "\n").trim();
  if (!text) return [];
  return text
    .replace(/\s*・\s*/g, "\n")
    .split(/\n+/)
    .map(line => line.replace(/^・+/, "").trim())
    .filter(Boolean);
}

// Merge unique bullet points across multiple articles for a combined digest
// view (related articles on article.html, cluster view on index.html).
function digestAcross(articles, limit = 5) {
  const seen = new Set();
  const items = [];
  for (const article of articles) {
    for (const point of summaryPoints(article.summary)) {
      const key = point.replace(/\s+/g, "").toLowerCase();
      if (!key || seen.has(key)) continue;
      seen.add(key);
      items.push(point);
      if (items.length >= limit) return items;
    }
  }
  return items;
}

// Build the shared "AI 綜合摘要" block. `prefix` picks the CSS namespace:
// "related" for article.html, "cluster" for index.html.
function aiSummaryBlockHtml(articles, prefix) {
  const digest = digestAcross(articles);
  const digestHtml = digest.length
    ? `<ul class="${prefix}-digest-list">${digest.map(p => `<li>${esc(p)}</li>`).join("")}</ul>`
    : `<div class="${prefix}-empty-summary">暫時未有足夠摘要</div>`;
  const sourceRows = articles.map(article => {
    const points = summaryPoints(article.summary).slice(0, 2);
    const pointsHtml = points.length
      ? `<div class="${prefix}-source-points">${points.map(p => `<div>${esc(p)}</div>`).join("")}</div>`
      : "";
    const ago = relativeTime(article.date);
    const agoHtml = ago ? `<span class="${prefix}-source-ago">${esc(ago)}</span>` : "";
    return `<div class="${prefix}-source-row">
      <div class="${prefix}-source-head">
        <span class="${prefix}-source-name">${esc(article.source || "未知來源")}</span>
        ${agoHtml}
        <span class="${prefix}-source-title">${esc(article.title || "")}</span>
      </div>
      ${pointsHtml}
    </div>`;
  }).join("");
  return { digestHtml, sourceRows };
}

// Shared between docs/js/redesign.js and docs/js/article-redesign.js.
// Define at common.js top level so each IIFE bundle inherits them via the
// global scope and we keep one source of truth.

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

// Detect when summary is just the article title (the placeholder shape
// `_ensure_analysis_defaults` writes on AI timeout). Real AI output is
// multi-bullet, so anything with a newline or two ・ separators is real
// content. Otherwise compare normalised forms.
function summaryIsTitleFallback(article) {
  if (!article || !article.summary || !article.title) return false;
  const raw = String(article.summary).trim();
  if (!raw) return false;
  const bulletCount = (raw.match(/・/g) || []).length;
  if (raw.includes("\n") || bulletCount >= 2) return false;
  const norm = (s) => String(s).replace(/^・/, "").replace(/\s+/g, "").trim();
  const s = norm(raw);
  const t = norm(article.title);
  if (!s || !t) return false;
  return s === t || (s.length >= 8 && t.startsWith(s));
}

// Composite ranking used by the homepage list and the article reader's
// related-stories panel. Higher is more critical.
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

function registerServiceWorker() {
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("sw.js").catch(() => {});
    });
  }
}
