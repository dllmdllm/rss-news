const _ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, c => _ESC[c]);
}

function safeUrl(u) {
  const s = String(u ?? "").trim();
  return /^https?:\/\//i.test(s) ? s : "#";
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
  const saved = localStorage.getItem(THEME_KEY);
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
    localStorage.setItem(THEME_KEY, theme);
    _applyTheme(theme);
    btn.innerHTML = _themeIcon(theme);
    btn.dataset.theme = theme;
  });
}

function setupTextOnlyMode() {
  const enabled = localStorage.getItem(TEXT_ONLY_KEY) === "1";
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
    localStorage.setItem(TEXT_ONLY_KEY, next ? "1" : "0");
    syncBtn(next);
  });
}

function setupFontSize() {
  let fsLevel = parseInt(localStorage.getItem("fontSize") ?? "1");
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
      localStorage.setItem("fontSize", fsLevel);
      applyFs();
    }
  });
  document.getElementById("font-dec").addEventListener("click", () => {
    if (fsLevel > 0) {
      fsLevel--;
      localStorage.setItem("fontSize", fsLevel);
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
  const text = String(summary || "").replace(/\r/g, "\n").trim();
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

function registerServiceWorker() {
  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("sw.js").catch(() => {});
    });
  }
}
