import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


def _extract_js_function(source: str, name: str) -> str:
    start = source.index(f"function {name}")
    brace = source.index("{", start)
    depth = 0
    for i in range(brace, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[start:i + 1]
    raise AssertionError(f"function {name} not closed")


def _require_node():
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    return node


@pytest.mark.parametrize(
    "script",
    [
        "docs/js/common.js",
        "docs/js/index.js",
        "docs/js/article.js",
        "docs/sw.js",
    ],
)
def test_frontend_javascript_syntax(script):
    node = _require_node()
    result = subprocess.run(
        [node, "--check", script],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_network_first_falls_back_to_cache_on_http_error():
    # 2026-07-21 audit finding：之前 networkFirst 淨係喺 fetch 本身 reject
    # （斷網/DNS/CORS）先 fallback 用 cache——伺服器有回應但係 4xx/5xx
    # （例如 GitHub Pages deploy race）會直接將錯誤 response 傳返俾頁面，
    # 完全繞過「network-first 但 cache 兜底」嘅設計原意。
    node = _require_node()
    source = (ROOT / "docs/sw.js").read_text(encoding="utf-8")
    js = "\n".join([
        'const CACHE = "test-cache";',
        _extract_js_function(source, "cacheKey"),
        _extract_js_function(source, "networkFirst"),
        """
        const cachedResponse = { ok: true, _tag: "cached" };
        const caches = {
          match: async (key) => cachedResponse,
          open: async (name) => ({ put: async () => {} }),
        };
        async function fetch(req) {
          return { ok: false, status: 503 };
        }
        (async () => {
          const req = { url: "https://example.com/data/articles.json" };
          const result = await networkFirst(req);
          if (result !== cachedResponse) {
            throw new Error("did not fall back to cache on HTTP error: " + JSON.stringify(result));
          }
        })();
        """,
    ])
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_stale_while_revalidate_never_responds_with_undefined():
    # 2026-07-21 audit finding：冇 cache entry（第一次見呢個 resource）
    # 又 fetch 失敗嘅話，之前會 resolve 做 undefined，
    # event.respondWith(undefined) 本身就違反 spec、會令個 resource load
    # 變成 hard network error。
    node = _require_node()
    source = (ROOT / "docs/sw.js").read_text(encoding="utf-8")
    js = "\n".join([
        'const CACHE = "test-cache";',
        _extract_js_function(source, "cacheKey"),
        _extract_js_function(source, "staleWhileRevalidate"),
        """
        const caches = {
          match: async (key) => undefined,
          open: async (name) => ({ put: async () => {} }),
        };
        async function fetch(req) {
          throw new Error("network down");
        }
        const Response = { error: () => ({ _tag: "network-error-response" }) };
        (async () => {
          const req = { url: "https://example.com/data/content/x.json" };
          const result = await staleWhileRevalidate(req);
          if (result === undefined) {
            throw new Error("staleWhileRevalidate resolved to undefined");
          }
        })();
        """,
    ])
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_graph_container_does_not_use_transform_animation():
    source = (ROOT / "docs" / "graph.html").read_text(encoding="utf-8")
    cy_rule = re.search(r"#cy\s*\{(?P<body>.*?)\n\s*\}", source, re.S)
    assert cy_rule, "#cy rule missing"
    body = cy_rule.group("body")
    assert "transform" not in body
    assert "animation" not in body


def test_graph_uses_saved_font_size_for_sidebar_and_nodes():
    source = (ROOT / "docs" / "graph.html").read_text(encoding="utf-8")
    assert 'localStorage.getItem("fontSize")' in source
    assert 'document.body.classList.add("fs-" + fs)' in source
    assert "body.fs-2 .sidebar-articles a" in source
    assert "graphScale = [1, 1.18, 1.38][fsLevel]" in source


def test_graph_has_time_and_node_limit_controls():
    source = (ROOT / "docs" / "graph.html").read_text(encoding="utf-8")
    assert 'class="filter-btn range-btn"' in source
    assert 'id="node-limit"' in source
    assert 'class="filter-btn type-btn active"' in source
    assert "nodeRecentCount" in source
    assert "edgeRecentCount" in source


def test_index_bootstrap_renders_articles_without_runtime_error():
    node = _require_node()
    js = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");

        class El {
          constructor(id) {
            this.id = id;
            this.innerHTML = "";
            this.textContent = "";
            this.className = "";
            this.dataset = {};
            this.style = {};
            this.tagName = "DIV";
            this.value = "";
            this.classList = {
              add: () => null,
              remove: () => null,
              contains: () => false,
              toggle: () => null,
            };
          }
          addEventListener() {}
          querySelector() { return null; }
          querySelectorAll() { return []; }
          insertAdjacentHTML(_pos, html) { this.innerHTML += html; }
          appendChild() {}
          remove() {}
          setAttribute() {}
        }

        const els = new Map();
        for (const id of [
          "categoryNav", "leadStory", "dailyBrief", "feed", "feedTitle",
          "resultCount", "priorityRange", "criticalList", "topicGrid",
          "sideSourceHealth", "sourceHealth", "sideUpdated", "aiUpdated",
          "fontTools", "modeNav", "search",
        ]) {
          els.set(id, new El(id));
        }

        const articles = [
          {
            id: "a1",
            title: "頭條新聞",
            url: "https://example.com/a1",
            date: "2026-05-22T10:00:00+00:00",
            source: "A",
            category: "新聞",
            summary: "・要點一\\n・要點二",
            score: 8,
          },
          {
            id: "a2",
            title: "國際大事",
            url: "https://example.com/a2",
            date: "2026-05-22T09:00:00+00:00",
            source: "B",
            category: "國際",
            summary: "・國際重點",
            score: 7,
          },
        ];

        const documentRoot = { style: { fontSize: "" } };
        const document = {
          body: new El("body"),
          documentElement: documentRoot,
          getElementById: id => els.get(id) || new El(id),
          createElement: tag => new El(tag),
          querySelector: () => ({ setAttribute() {} }),
          querySelectorAll: () => [],
          addEventListener() {},
        };
        const context = {
          console,
          document,
          window: { matchMedia: () => ({ matches: false }), addEventListener() {} },
          navigator: {},
          localStorage: { getItem: () => null, setItem() {} },
          setInterval() {},
          setTimeout,
          requestAnimationFrame: (cb) => setTimeout(cb, 0),
          IntersectionObserver: class { constructor() {} observe() {} disconnect() {} },
          Date, URL, encodeURIComponent, Number, String, Set, Map, RegExp, JSON, Math,
          Promise,
          fetch: async () => ({
            json: async () => ({ articles, trending_topics: [], sources: {}, updated: "2026-05-22" }),
          }),
        };
        context.globalThis = context;

        vm.runInNewContext(fs.readFileSync("docs/js/common.js", "utf8"), context);
        vm.runInNewContext(fs.readFileSync("docs/js/index.js", "utf8"), context);

        setTimeout(() => {
          const lead = els.get("leadStory").innerHTML;
          if (!lead || lead.length === 0) {
            throw new Error("leadStory was not rendered");
          }
          if (!lead.includes("頭條新聞")) {
            throw new Error("leadStory missing top article title: " + lead.slice(0, 200));
          }
        }, 50);
        """
    )
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_index_has_ai_sort_button():
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    if "js/index.js" in html:
        assert 'data-mode="critical"' in html
        assert 'data-mode="latest"' in html
        assert 'data-mode="balanced"' in html
        return
    assert 'data-sort="date"' in html
    assert ">🕒 最新</button>" in html
    assert 'data-sort="ai"' in html
    assert ">✨ 推薦</button>" in html
    assert 'data-sort="score"' not in html


def test_index_has_reading_controls_and_top_picks():
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    common_source = (ROOT / "docs/js/common.js").read_text(encoding="utf-8")
    if "js/index.js" in html:
        redesign_source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
        assert 'id="categoryNav"' in html
        assert 'id="leadStory"' in html
        assert 'id="dailyBrief"' in html
        assert 'id="criticalList"' in html
        assert 'id="priorityRange"' in html
        assert 'id="fontTools"' in html
        assert "rss_home_font_size" in redesign_source
        assert "priorityRange" in redesign_source
        assert "renderDailyBrief" in redesign_source
        return
    assert 'id="unread-toggle"' in html
    assert 'id="saved-toggle"' in html
    assert 'id="compact-toggle"' in html
    assert 'id="text-toggle"' in html
    assert 'id="top-picks"' in html
    assert "BOOKMARK_KEY" in source
    assert "MUTED_SOURCES_KEY" in source
    assert "DOWNRANK_SOURCES_KEY" in source
    assert "TEXT_ONLY_KEY" in common_source
    assert "parseSearchQuery" in source
    assert "score([<>]=?)" in source


def test_index_has_personalised_ai_alerts_and_uncertainty_badges():
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    if "js/index.js" in html:
        redesign_source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
        assert 'id="criticalList"' in html
        assert 'id="topicGrid"' in html
        assert "priorityBadge" in redesign_source
        assert "topicGrid" in redesign_source
        return
    assert 'id="ai-alerts"' in html
    assert ".ai-alerts" in html
    assert ".uncertainty-badge" in html
    assert "function buildPersonalProfile()" in source
    assert "function personalBoost(article, profile)" in source
    assert "function buildAiAlerts()" in source
    assert "uncertainty_flags" in source
    assert "risk:uncertain" not in source or "f.risk === \"uncertain\"" in source
    assert "AI 角度/時間線" in source


def test_index_mobile_filters_are_sheet_based_and_ai_picks_open_first():
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    if "js/index.js" in html:
        assert 'id="mobileTabs"' in html
        assert "mobile-home" in html
        assert "mobile-ai" in html
        assert "mobile-cat-chips" in html
        assert "mobile-search" in html
        assert "mobile-settings" in html
        return
    assert 'id="mobile-filter-toggle"' in html
    assert 'id="filter-backdrop"' in html
    assert "filter-sheet-open" in html
    assert "function setupMobileFilterSheet()" in source
    assert re.search(r"\blet\s+aiPicksOpen\s*=\s*true\b", source)
    assert "container.innerHTML = navHtml + stripHtml\n        + picksSection" in source


def test_frontend_avoids_inline_click_handlers():
    paths = [
        ROOT / "docs" / "index.html",
        ROOT / "docs" / "article.html",
        ROOT / "docs" / "graph.html",
        ROOT / "docs" / "upcoming.html",
        ROOT / "docs" / "entities.html",
    ]
    for path in paths:
        assert "onclick=" not in path.read_text(encoding="utf-8")


def test_index_latest_sort_orders_by_date():
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    common = (ROOT / "docs/js/common.js").read_text(encoding="utf-8")
    funcs = "\n".join([
        _extract_js_function(common, "criticalScore"),
        _extract_js_function(source, "sortedArticles"),
    ])
    js = funcs + """
    const state = { mode: "latest" };
    const articles = [
      { id: "old", date: "2026-04-20T10:00:00+08:00" },
      { id: "new", date: "2026-04-22T10:00:00+08:00" },
      { id: "mid", date: "2026-04-21T10:00:00+08:00" },
    ];
    const ids = sortedArticles(articles).map(a => a.id);
    if (JSON.stringify(ids) !== JSON.stringify(["new", "mid", "old"])) {
      throw new Error("latest sort was not date-desc: " + JSON.stringify(ids));
    }
    """
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_index_cluster_cards_are_stacked_and_click_to_expand():
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    if "js/index.js" in html:
        redesign_source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
        assert 'id="feed"' in html
        assert "grid-template-columns: repeat(2, minmax(0, 1fr))" in html
        assert "renderCategorySections" in redesign_source
        assert "CATEGORY_BASE_PER_GROUP = 4" in redesign_source
        assert "fillCategoriesToMatchBrief" in redesign_source
        return
    assert ".card.cluster-stack" in html
    assert ".card.cluster-expanded" in html
    assert ".cluster-ai-btn" in html
    assert ".cluster-ai-summary" in html
    assert "body.fs-0 .cluster-ai-summary" in html
    assert "body.fs-1 .cluster-ai-summary" in html
    assert "body.fs-2 .cluster-ai-summary" in html
    assert 'isClusterStack ? `#cluster-${cid}`' in source
    assert 'data-card-action="filter-cluster"' in source
    assert 'data-card-action="toggle-summary"' in source
    assert 'function handleCardAction(action, el, event)' in source
    assert "點擊展開" in source
    assert "點擊收起" in source
    assert "function collapseCluster" in source
    assert "AI 綜合摘要" in source
    assert "function clusterSummaryHtml(cid" in source
    assert 'clusterSummaryHtml(cid, "body")' in source


def test_payload_is_complete_rejects_empty_sources_and_articles():
    # 2026-07-21 audit finding: `!{}` / `![].length` 喺 JS 都係 false，
    # 之前空 sources object 同空 articles array 都會被當「完整」放行，
    # 唔會 fallback 去 articles.json。
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    fn = _extract_js_function(source, "payloadIsComplete")
    js = fn + """
    const complete = {
      articles: [{ summary: "x" }],
      sources: { "明報": {} },
      trending_topics: [],
    };
    const emptySources = { ...complete, sources: {} };
    const emptyArticles = { ...complete, articles: [] };
    const noSources = { ...complete, sources: null };

    if (payloadIsComplete(complete) !== true) throw new Error("complete payload rejected");
    if (payloadIsComplete(emptySources) !== false) throw new Error("empty sources accepted");
    if (payloadIsComplete(emptyArticles) !== false) throw new Error("empty articles accepted");
    if (payloadIsComplete(noSources) !== false) throw new Error("null sources accepted");
    if (payloadIsComplete(null) !== false) throw new Error("null payload accepted");
    """
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_storage_get_survives_localstorage_securityerror():
    # 2026-07-21 audit finding: state.mobile 初始化之前有 7 個 unguarded
    # localStorage.getItem call，係 module 頂層 code，喺任何 function 執行
    # 之前就跑——封鎖 cookies/私隱模式環境 access localStorage 本身就會
    # throw SecurityError，令成個 script 死晒，page 睇落「載入咗但係空白」。
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    fn = _extract_js_function(source, "storageGet")
    js = fn + """
    const localStorage = {
      getItem() { throw new DOMException("blocked", "SecurityError"); },
    };
    const result = storageGet("mobile.view", "home");
    if (result !== "home") throw new Error("storageGet threw or ignored fallback: " + result);
    """
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_common_js_storage_get_survives_localstorage_securityerror():
    # 2026-07-21 audit finding: setupThemeMode/setupTextOnlyMode/
    # setupFontSize 之前直接 call localStorage.getItem，喺 entities.html/
    # upcoming.html/graph.html page-init 時執行——封鎖 cookies 環境會令
    # 呢幾個 page 初始化失敗，同 index.js 果個已經修好嘅 bug 係同一類。
    node = _require_node()
    source = (ROOT / "docs/js/common.js").read_text(encoding="utf-8")
    fn = _extract_js_function(source, "storageGet")
    js = fn + """
    const localStorage = {
      getItem() { throw new DOMException("blocked", "SecurityError"); },
    };
    const result = storageGet("rss_theme", "dark");
    if (result !== "dark") throw new Error("storageGet threw or ignored fallback: " + result);
    """
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_index_cluster_digest_dedupes_summary_points():
    node = _require_node()
    common = (ROOT / "docs/js/common.js").read_text(encoding="utf-8")
    js = "\n".join([
        _extract_js_function(common, "summaryPoints"),
        _extract_js_function(common, "digestAcross"),
        """
        const rows = digestAcross([
          { source: "A", summary: "・共同重點\\n・A角度" },
          { source: "B", summary: "共同重點\\nB角度" },
        ]);
        const expected = ["共同重點", "A角度", "B角度"];
        if (JSON.stringify(rows) !== JSON.stringify(expected)) {
          throw new Error("bad digest rows: " + JSON.stringify(rows));
        }
        """,
    ])
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_index_summary_points_normalise_bullets():
    node = _require_node()
    source = (ROOT / "docs/js/common.js").read_text(encoding="utf-8")
    fn = _extract_js_function(source, "summaryPoints")
    js = fn + """
    const cases = [
      ["・one・two", ["one", "two"]],
      ["one\\ntwo", ["one", "two"]],
      [" ・one\\n・two ", ["one", "two"]],
    ];
    for (const [input, expected] of cases) {
      const actual = summaryPoints(input);
      if (JSON.stringify(actual) !== JSON.stringify(expected)) {
        throw new Error("bad summary points: " + JSON.stringify(actual));
      }
    }
    """
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_article_page_applies_saved_light_theme():
    node = _require_node()
    common = (ROOT / "docs/js/common.js").read_text(encoding="utf-8")
    js = common + textwrap.dedent(
        """
        const classes = new Set();
        let themeColor;
        const document = {
          body: {
            classList: {
              toggle(name, on) {
                if (on) classes.add(name);
                else classes.delete(name);
              },
            },
          },
          getElementById: () => null,
          querySelector: () => ({ setAttribute(_, value) { themeColor = value; } }),
        };
        const localStorage = { getItem: key => (key === "rss_theme" ? "light" : null), setItem() {} };
        const window = { matchMedia: () => ({ matches: false }) };
        setupThemeMode();
        if (!classes.has("theme-light") || classes.has("theme-dark")) {
          throw new Error("saved light theme was not applied");
        }
        if (themeColor !== "#fafaf8") {
          throw new Error("theme color not updated: " + themeColor);
        }
        """
    )
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_article_has_text_only_toggle():
    html = (ROOT / "docs/article.html").read_text(encoding="utf-8")
    common_source = (ROOT / "docs/js/common.js").read_text(encoding="utf-8")
    if "js/article.js" in html:
        article_source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
        assert 'id="fontSmall"' in html
        assert 'id="fontNormal"' in html
        assert 'id="fontLarge"' in html
        assert "fs-small" in article_source
        assert "fs-large" in article_source
        return
    assert 'id="text-toggle"' in html
    assert "TEXT_ONLY_KEY" in common_source
    assert "body.text-only .content img" in html


def test_article_back_uses_same_origin_history_only():
    html = (ROOT / "docs/article.html").read_text(encoding="utf-8")
    source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    if "js/article.js" in html:
        assert 'href="index.html"' in html
        assert 'class="back"' in html
        assert 'class="mobile-back"' in html
        return
    assert 'data-safe-back="1"' in html
    assert "function setupSafeBackLinks()" in source
    assert "ref.origin === location.origin" in source
    assert "history.back();" in source


def test_article_text_only_mode_applies_body_class():
    node = _require_node()
    js = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");

        class El {
          constructor(id) {
            this.id = id;
            this.innerHTML = "";
            this.textContent = "";
            this.className = "";
            this.dataset = {};
            this.style = {};
            this.tagName = "DIV";
            this.value = "";
            this.href = "";
            this._listeners = {};
            const self = this;
            this._classes = new Set();
            this.classList = {
              add: (...names) => { names.forEach(n => self._classes.add(n)); },
              remove: (...names) => { names.forEach(n => self._classes.delete(n)); },
              contains: name => self._classes.has(name),
              toggle: (name, force) => {
                const next = force === undefined ? !self._classes.has(name) : !!force;
                if (next) self._classes.add(name);
                else self._classes.delete(name);
                return next;
              },
            };
          }
          addEventListener(type, fn) { (this._listeners[type] = this._listeners[type] || []).push(fn); }
          dispatch(type) { (this._listeners[type] || []).forEach(fn => fn()); }
          setAttribute() {}
        }

        const els = new Map();
        for (const id of [
          "updated", "metaList", "sourceList", "readTime",
          "fontSmall", "fontNormal", "fontLarge", "textOnly",
          "eyebrow", "title", "dek", "hero", "summaryBox", "content",
          "sourceLink", "priorityNote", "facts", "relatedList",
        ]) {
          els.set(id, new El(id));
        }

        const bodyClasses = new Set();
        const body = {
          className: "",
          dataset: {},
          classList: {
            add: (...names) => { names.forEach(n => bodyClasses.add(n)); body.className = [...bodyClasses].join(" "); },
            remove: (...names) => { names.forEach(n => bodyClasses.delete(n)); body.className = [...bodyClasses].join(" "); },
            contains: name => bodyClasses.has(name),
            toggle: (name, force) => {
              const next = force === undefined ? !bodyClasses.has(name) : !!force;
              if (next) bodyClasses.add(name);
              else bodyClasses.delete(name);
              body.className = [...bodyClasses].join(" ");
              return next;
            },
          },
        };

        const document = {
          body,
          getElementById: id => els.get(id) || new El(id),
          querySelector: () => ({ setAttribute() {} }),
          querySelectorAll: () => [],
          addEventListener() {},
        };

        class FakeDOMParser {
          parseFromString() {
            return {
              body: { innerHTML: "", textContent: "" },
              querySelectorAll: () => [],
              querySelector: () => null,
            };
          }
        }

        const context = {
          console,
          document,
          window: { matchMedia: () => ({ matches: false }), addEventListener() {}, scrollTo() {} },
          navigator: {},
          localStorage: { getItem: () => null, setItem() {} },
          setTimeout,
          Date, URL, URLSearchParams, encodeURIComponent, Number, String, Set, Map, RegExp, JSON, Math,
          Promise,
          DOMParser: FakeDOMParser,
          history: { replaceState() {} },
          location: { search: "?id=abc", href: "https://example.com/article.html?id=abc" },
          fetch: async () => ({
            ok: true,
            json: async () => ({
              articles: [{ id: "abc", title: "測試文章", source: "來源", date: "2026-04-27T00:00:00+00:00", category: "新聞", url: "https://example.com/abc" }],
              sources: {},
              updated: "2026-04-27",
            }),
          }),
        };
        context.globalThis = context;

        vm.runInNewContext(fs.readFileSync("docs/js/common.js", "utf8"), context);
        vm.runInNewContext(fs.readFileSync("docs/js/article.js", "utf8"), context);

        setTimeout(() => {
          els.get("textOnly").dispatch("click");
          if (!body.classList.contains("text-only")) {
            throw new Error("expected body.text-only to be applied after click");
          }
          if (!els.get("textOnly")._classes.has("active")) {
            throw new Error("expected textOnly button to gain 'active' class");
          }
        }, 50);
        """
    )
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_article_share_uses_original_source_url():
    source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    assert '$("sourceLink").href = article.url' in source


def test_article_page_has_related_section():
    html = (ROOT / "docs/article.html").read_text(encoding="utf-8")
    source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    if "js/article.js" in html:
        article_source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
        assert 'id="relatedList"' in html
        assert "relatedArticles" in article_source
        assert "renderMiniArticle" in article_source
        return
    assert 'id="related-section"' in html
    assert 'id="related-toggle"' in html
    assert 'id="related-ai-summary"' in html
    assert 'class="related-list" id="related-list"' in html
    assert "body.fs-0 .related-list" in html
    assert "body.fs-1 .related-list" in html
    assert "body.fs-2 .related-list" in html
    assert "body.fs-0 .related-ai-summary" in html
    assert "body.fs-1 .related-ai-summary" in html
    assert "body.fs-2 .related-ai-summary" in html
    assert "相關新聞" in html
    assert "AI 綜合摘要" in html
    assert "renderRelatedArticles(art, data.articles," in source
    assert "relatedSummaryHtml([current, ...rows.map(row => row.article)])" in source
    assert "summary.classList.toggle(\"show\")" in source
    assert "toggle.setAttribute(\"aria-expanded\", \"false\")" in source


def test_article_redesign_removes_duplicate_hero_image():
    source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    assert "function canonicalImageUrl" in source
    assert "sanitizeHtml(article.content, article.thumbnail || \"\")" in source
    assert "firstImage.remove()" in source


def test_article_page_has_save_and_next_unread_controls():
    html = (ROOT / "docs/article.html").read_text(encoding="utf-8")
    source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    if "js/article.js" in html:
        assert 'id="fontSmall"' in html
        assert 'id="fontNormal"' in html
        assert 'id="fontLarge"' in html
        assert 'id="relatedList"' in html
        return
    assert 'id="save-btn"' in html
    assert 'id="next-unread-btn"' in html
    assert ".image-fallback" in html
    assert "setupSaveButton(id);" in source
    assert "setupNextUnread(id, data.articles);" in source
    assert "readJsonSet(BOOKMARK_KEY)" in source


def test_article_related_digest_dedupes_summary_points():
    node = _require_node()
    common = (ROOT / "docs/js/common.js").read_text(encoding="utf-8")
    js = "\n".join([
        _extract_js_function(common, "summaryPoints"),
        _extract_js_function(common, "digestAcross"),
        """
        const rows = digestAcross([
          { source: "A", summary: "・共同重點\\n・A角度" },
          { source: "B", summary: "共同重點\\nB角度" },
        ]);
        const expected = ["共同重點", "A角度", "B角度"];
        if (JSON.stringify(rows) !== JSON.stringify(expected)) {
          throw new Error("bad digest rows: " + JSON.stringify(rows));
        }
        """,
    ])
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_ai_rank_score_prioritises_importance_cluster_and_recency():
    node = _require_node()
    source = (ROOT / "docs/js/common.js").read_text(encoding="utf-8")
    js = "\n".join([
        _extract_js_function(source, "criticalScore"),
        """
        const nowIso = new Date().toISOString();
        const dayAgoIso = new Date(Date.now() - 24 * 3600 * 1000).toISOString();
        const weekAgoIso = new Date(Date.now() - 7 * 24 * 3600 * 1000).toISOString();

        const high = criticalScore({ title: "經濟報告", score: 9, cluster_size: 3, date: dayAgoIso });
        const lowButFresh = criticalScore({ title: "輕新聞", score: 4, cluster_size: 1, date: nowIso });
        const clustered = criticalScore({ title: "事件追蹤", score: 6, cluster_size: 4, date: weekAgoIso });
        const solo = criticalScore({ title: "事件追蹤", score: 6, cluster_size: 1, date: weekAgoIso });
        const breaking = criticalScore({ title: "突發：嚴重火警", score: 6, cluster_size: 1, date: weekAgoIso });
        const calm = criticalScore({ title: "市場分析", score: 6, cluster_size: 1, date: weekAgoIso });

        if (high <= lowButFresh) throw new Error("importance should dominate: high=" + high + " low=" + lowButFresh);
        if (clustered <= solo) throw new Error("cluster bonus missing: clustered=" + clustered + " solo=" + solo);
        if (breaking <= calm) throw new Error("breaking keyword boost missing: breaking=" + breaking + " calm=" + calm);
        """,
    ])
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_service_worker_cache_key_strips_query_string():
    node = _require_node()
    js = textwrap.dedent(
        """
        const fs = require("fs");
        const vm = require("vm");
        const code = fs.readFileSync("docs/sw.js", "utf8");
        const context = {
          URL,
          location: { origin: "https://example.com" },
          caches: {
            open: async () => ({ addAll: async () => null, put: async () => null }),
            keys: async () => [],
            delete: async () => null,
            match: async () => null,
          },
          self: {
            addEventListener: () => null,
            skipWaiting: () => null,
            clients: { claim: () => null },
          },
          fetch: async () => ({ ok: true, clone: () => ({}) }),
        };
        vm.runInNewContext(code, context);
        const key = context.cacheKey({ url: "https://example.com/data/articles.json?12345" });
        if (key !== "https://example.com/data/articles.json") {
          throw new Error("unexpected cache key: " + key);
        }
        """
    )
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr
