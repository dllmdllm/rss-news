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


def test_index_and_categories_css_use_matching_category_colors():
    # 2026-07-21 audit finding: docs/index.html 自己有一套獨立
    # --cat-color 系統，同 docs/css/categories.css 嘅 --cat-rgb 完全冇連
    # 過，同一分類撞色（例如「新聞」index.html 顯示藍色，categories.css
    # 係紅色）。而家兩邊已經手動同步咗做同一組顏色，呢個 test 防止
    # 之後單改一邊而唔記得改另一邊、悄悄地又拆返兩套。
    index_html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    categories_css = (ROOT / "docs/css/categories.css").read_text(encoding="utf-8")

    slug_to_zh = {
        "cat-news": "新聞", "cat-world": "國際", "cat-ent": "娛樂",
        "cat-tech": "科技", "cat-life": "消閒", "cat-media": "網媒",
    }
    hex_by_slug = dict(re.findall(r'\.(cat-\w+)\s*\{\s*--cat-color:\s*#([0-9a-fA-F]{6});', index_html))
    assert len(hex_by_slug) == 6, f"expected 6 index.html category colours, found {hex_by_slug}"

    rgb_by_zh = dict(re.findall(
        r'body\.cat-([^\s,]+),\s*\[data-cat="[^"]+"\]\s*\{\s*--cat-rgb:\s*([\d\s]+);',
        categories_css,
    ))
    assert len(rgb_by_zh) == 6, f"expected 6 categories.css dark-theme colours, found {rgb_by_zh}"

    for slug, hex_val in hex_by_slug.items():
        zh = slug_to_zh[slug]
        expected_rgb = f"{int(hex_val[0:2], 16)} {int(hex_val[2:4], 16)} {int(hex_val[4:6], 16)}"
        actual_rgb = rgb_by_zh[zh].strip()
        assert actual_rgb == expected_rgb, (
            f"{zh} ({slug}): index.html #{hex_val} = rgb({expected_rgb}) "
            f"but categories.css has rgb({actual_rgb})"
        )


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


def _tts_chunk_max() -> int:
    """讀 production 真實常數，唔好喺 test 度自己寫死一個——
    寫死嘅話有人將 production 改返做 9999，test 一樣照 pass（親身踩過）。"""
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    m = re.search(r"const TTS_CHUNK_MAX = (\d+);", source)
    assert m, "揾唔到 TTS_CHUNK_MAX"
    return int(m.group(1))


def test_tts_chunk_max_stays_within_ios_safe_range():
    # iOS Safari 對長 utterance 會靜靜哋失敗，實測安全範圍大約 200 字以下。
    assert 40 <= _tts_chunk_max() <= 200, (
        "TTS_CHUNK_MAX 超出 iOS 安全範圍——正正係 2026-07-25『ios 聽早報冇聲』"
        "嗰個 bug（當時成篇 487 字塞一個 utterance）"
    )


def test_split_for_tts_chunks_stay_under_ios_limit():
    # iOS Safari 對長 utterance 會靜靜哋唔出聲／讀到一半死（2026-07-25 用戶
    # 報告「ios 聽早報冇聲」，實測當時全文 487 字塞晒落一個 utterance）。
    # 桌面 Chrome 冇事，所以之前一直冇為意。
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    js = "\n".join([
        f"const TTS_CHUNK_MAX = {_tts_chunk_max()};",
        _extract_js_function(source, "splitForTts"),
        """
        const brief = "港聞一。".repeat(60) + "最後一句冇句號收尾";
        const chunks = splitForTts(brief);
        if (!chunks.length) throw new Error("no chunks produced");
        for (const c of chunks) {
          if (c.length > TTS_CHUNK_MAX) {
            throw new Error("chunk too long: " + c.length);
          }
        }
        // 冇漏字：接返埋要同原文一樣
        if (chunks.join("") !== brief) {
          throw new Error("text lost while chunking");
        }
        """,
    ])
    result = subprocess.run([node, "-e", js], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_split_for_tts_handles_one_giant_unpunctuated_sentence():
    # 斷句靠標點，但一句冇標點嘅超長句唔可以令個 chunk 爆上限。
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    js = "\n".join([
        f"const TTS_CHUNK_MAX = {_tts_chunk_max()};",
        _extract_js_function(source, "splitForTts"),
        """
        const chunks = splitForTts("字".repeat(500));
        if (chunks.some((c) => c.length > TTS_CHUNK_MAX)) {
          throw new Error("hard split failed");
        }
        if (chunks.join("").length !== 500) throw new Error("length changed");
        """,
    ])
    result = subprocess.run([node, "-e", js], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_morning_brief_is_collapsible():
    # 用戶要求「今日早報想可以一 click 就收埋」。
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    assert 'id="briefToggle"' in source, "缺少摺疊掣"
    assert 'class="morning-brief-body"' in source, "缺少可摺疊嘅 body wrapper"
    assert "BRIEF_COLLAPSE_KEY" in source, "摺疊狀態要 persist"
    css = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    assert ".morning-brief.collapsed .morning-brief-body" in css, "缺少收埋嘅 CSS"


def test_brief_tts_button_does_not_toggle_collapse():
    # TTS 掣坐喺可撳嘅標題行入面，冇 stopPropagation 就會撳播放連帶摺埋。
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    idx = source.index('$("briefTts")?.addEventListener')
    handler = source[idx:idx + 220]
    assert "stopPropagation" in handler, "TTS 掣要 stopPropagation"


def test_source_health_heading_not_duplicated():
    # 2026-07-25 用戶影相：手機設定頁見到兩個「來源健康」。
    # renderMobileSideHealth() 直接 copy sideSourceHealth 嘅 innerHTML 落
    # 一個已經有 <h2>來源健康</h2> 嘅 section，所以個 innerHTML 唔可以自帶標題。
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    idx = source.index('$("sideSourceHealth").innerHTML =')
    assign = source[idx:idx + 160]
    assert "來源健康" not in assign, "sideSourceHealth 唔應該自帶標題（手機會重複）"


# ── AI tab：2026-07-25 review 發現生成咗但冇出街嘅 AI 數據 ──

def test_ai_rail_surfaces_tension_not_only_contradictions():
    # `contradictions` 只有約 3/7 個 topic 有，`tension` 7/7 都有但一直冇讀，
    # 所以嗰格成日空白，令成個 AI tab 睇落淨係得「排序過嘅新聞清單」。
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    fn = _extract_js_function(source, "renderContradictions")
    assert "digest.tension" in fn or "digest.contradictions" in fn
    assert "tension" in fn, "renderContradictions 要一齊出 tension"


def test_ai_rail_renders_timeline():
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    assert "function renderTimeline" in source, "缺少事件時間軸 render"
    fn = _extract_js_function(source, "renderTimeline")
    assert "digest.timeline" in fn or "d.timeline" in fn or "digest && digest.timeline" in fn
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    assert 'id="timelineBlock"' in html and 'id="timelineList"' in html


def test_mood_block_counts_all_three_sentiments():
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    js = "\n".join([
        """
        const state = { articles: [], sentiment: "" };
        const MOOD_META = { negative:{label:"負面"}, neutral:{label:"中性"}, positive:{label:"正面"} };
        const nodes = {};
        function $(id){ return nodes[id] || (nodes[id] = { hidden: true, textContent: "", innerHTML: "" }); }
        function esc(s){ return String(s); }
        """,
        _extract_js_function(source, "renderMood"),
        """
        const list = [
          {sentiment:"negative"},{sentiment:"negative"},{sentiment:"negative"},
          {sentiment:"neutral"},
          {sentiment:"positive"},
          {sentiment:undefined},
        ];
        renderMood(list);
        if ($("moodBlock").hidden) throw new Error("block stayed hidden");
        if ($("moodCount").textContent !== "5 篇") {
          throw new Error("bad total: " + $("moodCount").textContent);
        }
        const html = $("moodBar").innerHTML;
        for (const k of ["negative","neutral","positive"]) {
          if (!html.includes('data-sentiment="' + k + '"')) throw new Error("missing chip " + k);
        }
        if (!html.includes("60%")) throw new Error("negative should be 60%: " + html);
        """,
    ])
    result = subprocess.run([node, "-e", js], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_mood_block_hides_when_no_sentiment_data():
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    js = "\n".join([
        """
        const state = { articles: [], sentiment: "" };
        const MOOD_META = { negative:{label:"負面"}, neutral:{label:"中性"}, positive:{label:"正面"} };
        const nodes = {};
        function $(id){ return nodes[id] || (nodes[id] = { hidden: true, textContent: "", innerHTML: "" }); }
        function esc(s){ return String(s); }
        """,
        _extract_js_function(source, "renderMood"),
        """
        renderMood([{title:"冇 sentiment"}]);
        if (!$("moodBlock").hidden) throw new Error("should stay hidden with no data");
        """,
    ])
    result = subprocess.run([node, "-e", js], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_sentiment_filter_is_wired_into_filtering_and_has_an_exit():
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    fn = _extract_js_function(source, "filteredArticles")
    assert "state.sentiment" in fn, "sentiment 要真係參與篩選"
    # 篩咗之後一定要有退出方式，否則用戶困死喺 filter 狀態
    assert 'id="clearSentiment"' in source, "缺少清除輿情篩選嘅掣"
    assert 'closest("#clearSentiment")' in source, "清除掣冇 handler"


def test_related_articles_prefers_semantic_similar_map():
    # similar.json（embed.py 計嘅 cosine top-5）一直生成咗但全站冇讀
    # （2026-07-25 review）。而家文章頁「相關新聞」優先用佢，heuristic 補位。
    node = _require_node()
    source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    js = "\n".join([
        'function titleKey(a){ return String((a && a.title) || "").replace(/\s+/g, "").slice(0, 18); }',
        _extract_js_function(source, "relatedArticles"),
        """
        const current = { id: "cur", title: "颱風襲港", topic: "天氣", category: "新聞", source: "A", tags: [] };
        const articles = [
          current,
          { id: "sem1", title: "教育局宣布停課", topic: "教育", category: "教育", source: "B", tags: [] },
          { id: "sem2", title: "天文台改發三號", topic: "天氣", category: "新聞", source: "C", tags: [] },
          { id: "heur", title: "同分類同來源但唔啱題", topic: "天氣", category: "新聞", source: "A", tags: [] },
        ];
        const similarMap = { cur: ["sem1", "sem2"] };
        const out = relatedArticles(current, articles, new Set(), 3, similarMap);
        const ids = out.map((a) => a.id);
        if (ids[0] !== "sem1" || ids[1] !== "sem2") {
          throw new Error("語義結果要行先: " + JSON.stringify(ids));
        }
        if (out.length !== 3 || ids[2] !== "heur") {
          throw new Error("heuristic 要補返尾位: " + JSON.stringify(ids));
        }
        """,
    ])
    result = subprocess.run([node, "-e", js], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_related_articles_falls_back_without_similar_map():
    # similar.json fetch 失敗／新文仲未 embed 就要靜靜降級，唔可以變空白。
    node = _require_node()
    source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    js = "\n".join([
        'function titleKey(a){ return String((a && a.title) || "").replace(/\s+/g, "").slice(0, 18); }',
        _extract_js_function(source, "relatedArticles"),
        """
        const current = { id: "cur", title: "A", topic: "天氣", category: "新聞", source: "A", tags: [] };
        const articles = [current, { id: "x", title: "B", topic: "天氣", category: "新聞", source: "B", tags: [] }];
        if (relatedArticles(current, articles, new Set(), 3, null).length !== 1) {
          throw new Error("冇 similarMap 就要用返 heuristic");
        }
        """,
    ])
    result = subprocess.run([node, "-e", js], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_related_articles_never_exceeds_limit():
    node = _require_node()
    source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    js = "\n".join([
        'function titleKey(a){ return String((a && a.title) || "").replace(/\s+/g, "").slice(0, 18); }',
        _extract_js_function(source, "relatedArticles"),
        """
        const current = { id: "cur", title: "A", topic: "T", category: "C", source: "S", tags: [] };
        const articles = [current];
        const sim = { cur: [] };
        for (let i = 0; i < 20; i++) {
          articles.push({ id: "s" + i, title: "sem" + i, topic: "T", category: "C", source: "S", tags: [] });
          sim.cur.push("s" + i);
        }
        const out = relatedArticles(current, articles, new Set(), 6, sim);
        if (out.length !== 6) throw new Error("limit 爆咗: " + out.length);
        """,
    ])
    result = subprocess.run([node, "-e", js], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_mobile_ai_tab_has_three_subtabs():
    # 用戶要求（2026-07-25）：手機 AI tab 頂部俾三頁揀。之前得兩個
    # （優先／分類重點），而分析欄係硬疊喺清單下面一路捲。
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    block = html[html.index('id="mobileSubAi"'):]
    block = block[:block.index("</div>")]
    for mode in ("priority", "category", "analysis"):
        assert f'data-ai-mode="{mode}"' in block, f"缺少 {mode} 分頁"


def test_mobile_analysis_tab_swaps_rail_for_list():
    # 三個分頁要真係二選一顯示，唔係又疊返一齊。
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    assert "body.mobile-ai.ai-mode-analysis .main { display: none; }" in html
    assert "body.mobile-ai:not(.ai-mode-analysis) .ai { display: none; }" in html
    js = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    fn = _extract_js_function(js, "updateMobileSubUi")
    # 驗行為唔驗寫法：三個 mode 都要覆蓋，個 class 要跟 aiMode 走。
    assert "ai-mode-" in fn, "updateMobileSubUi 要 toggle ai-mode-* body class"
    for mode in ("priority", "category", "analysis"):
        assert mode in fn, f"updateMobileSubUi 冇處理 {mode}"
    assert "state.mobile.aiMode" in fn


def test_analysis_mode_does_not_leak_category_filter():
    # analysis 同 priority 一樣係全量檢視——唔應該帶住 aiCat/aiSource 篩選。
    js = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    fn = _extract_js_function(js, "syncStateFromMobile")
    assert 'state.mobile.aiMode === "category" ? state.mobile.aiCat : "全部"' in fn
    assert 'state.mobile.aiMode === "category" ? state.mobile.aiSource : ""' in fn


def test_entity_block_mixes_types_instead_of_all_places():
    # 地點嘅 count 遠高過人物/機構（「香港」128 篇），照 count 排就成格得曬
    # 地點，冇資訊量。每類最多 2 個。
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    js = "\n".join([
        """
        const state = { entity: "", entityIndex: null };
        const nodes = {};
        function $(id){ return nodes[id] || (nodes[id] = { hidden: true, innerHTML: "", textContent: "" }); }
        function esc(s){ return String(s); }
        const ENTITY_TYPE_ICON = { people:"👤", companies:"🏢", places:"📍" };
        """,
        _extract_js_function(source, "renderEntities"),
        """
        const entities = [];
        for (let i = 0; i < 5; i++) entities.push({ type:"places", name:"地"+i, count:100-i, article_ids:["a"] });
        for (let i = 0; i < 5; i++) entities.push({ type:"people", name:"人"+i, count:10-i, article_ids:["b"] });
        for (let i = 0; i < 5; i++) entities.push({ type:"companies", name:"司"+i, count:9-i, article_ids:["c"] });
        renderEntities({ entities });
        if ($("entityBlock").hidden) throw new Error("block 應該顯示");
        const html = $("entityList").innerHTML;
        const n = (re) => (html.match(re) || []).length;
        if (n(/👤/g) !== 2 || n(/🏢/g) !== 2 || n(/📍/g) !== 2) {
          throw new Error("每類要 2 個: " + html);
        }
        """,
    ])
    result = subprocess.run([node, "-e", js], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_entity_filter_uses_article_ids_not_name_matching():
    # AI 抽到嘅實體名唔一定逐字出現喺標題／摘要，夾字會漏一大截——
    # 一定要用 entities.json 記低嘅 article_ids。
    js = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    fn = _extract_js_function(js, "filteredArticles")
    assert "article_ids" in fn, "實體篩選要用 article_ids"
    assert "entityIds" in fn


def test_entity_filter_has_an_exit():
    js = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    assert 'id="clearEntity"' in js
    assert 'closest("#clearEntity")' in js


def test_key_sentences_sit_under_the_ai_summary():
    # 用戶要求（2026-07-25）：關鍵句由右欄搬去 AI 摘要下面——兩樣都係
    # 「睇正文之前想知」，之前要撳 AI 掣展開右欄先見到。
    html = (ROOT / "docs/article.html").read_text(encoding="utf-8")
    summary_at = html.index('id="summaryBox"')
    facts_at = html.index('id="facts"')
    content_at = html.index('id="content"')
    assert summary_at < facts_at < content_at, "關鍵句要喺 AI 摘要同正文之間"
    # 唔可以仲留喺右欄
    aside = html[html.index('<aside class="ai">'):]
    assert 'id="facts"' not in aside, "關鍵句唔應該仲喺右欄"


def test_key_sentences_are_not_line_clamped():
    # 關鍵句係原文逐字摘錄（10-80 字），clamp 落 2 行會變「……」睇唔到重點。
    html = (ROOT / "docs/article.html").read_text(encoding="utf-8")
    block_start = html.index(".fact-list li")
    block = html[block_start:block_start + 220]
    assert "-webkit-line-clamp" not in block, "關鍵句唔應該有 line-clamp"


def test_key_facts_block_hides_when_empty():
    js = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    assert '$("keyFacts").hidden' in js, "冇關鍵句就要收起成個框"


def test_ai_priority_and_category_tabs_show_different_things():
    # 用戶反映「一 click 入去優先 tab、同分類重點 tab 都係 show 優先排行，
    # 好混亂」——.brief 一次過 render 兩樣嘢，兩個 tab 分唔開。
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    assert "brief-priority" in html and "brief-category" in html, "兩截要分開包住"
    assert "body.mobile-ai.ai-mode-priority .brief-category { display: none; }" in html
    assert "body.mobile-ai.ai-mode-category .brief-priority { display: none; }" in html


def test_source_health_groups_sections_into_outlets():
    # 用戶反映（2026-07-25）：「星島係包晒分類，明報、東網又分本地/娛樂」——
    # 粒度唔一致。同一間媒體嘅版面 feed 要合併返做一行。
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    js = "\n".join([
        'const OUTLET_PREFIXES = ["RTHK", "明報", "東網", "HK01", "Now"];',
        _extract_js_function(source, "outletOf"),
        _extract_js_function(source, "groupSourcesByOutlet"),
        """
        const rows = groupSourcesByOutlet({
          "明報 本地": { effective_count: 30 },
          "明報 娛樂": { effective_count: 13 },
          "東網 本地": { effective_count: 30 },
          "星島頭條":   { effective_count: 100 },
          "New MobileLife":    { effective_count: 15 },
          "The Collective HK": { effective_count: 3 },
        });
        const by = Object.fromEntries(rows.map((r) => [r.outlet, r]));
        if (by["明報"].count !== 43 || by["明報"].feeds !== 2) {
          throw new Error("明報 冇合併: " + JSON.stringify(by["明報"]));
        }
        if (!by["星島頭條"] || by["星島頭條"].feeds !== 1) throw new Error("星島 唔應該被拆");
        // 名入面有空格但唔係版面 feed 嘅，唔可以照空格斬
        if (!by["New MobileLife"]) throw new Error("New MobileLife 俾人斬咗");
        if (!by["The Collective HK"]) throw new Error("The Collective HK 俾人斬咗");
        if (rows[0].outlet !== "星島頭條") throw new Error("要按篇數排序");
        """,
    ])
    result = subprocess.run([node, "-e", js], cwd=ROOT, text=True, capture_output=True)
    assert result.returncode == 0, result.stderr


def test_source_health_shows_every_outlet():
    # 之前 slice(0, 8)，34 個 feed 得 8 個見到——明報 5 個版面合共 85 篇
    # 但每個 ≤30，喺榜上完全消失。
    js = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    fn = _extract_js_function(js, "renderAiPanel")
    # 只睇 sources 嗰一句——同一個 function 入面 critical/topicGrid 都有
    # slice，嗰啲係故意嘅（優先排行 top 8、話題 6 個）。
    line = next(l for l in fn.splitlines() if "groupSourcesByOutlet" in l)
    assert "slice" not in line, f"來源健康唔應該再 cap: {line.strip()}"


def test_article_rail_has_no_empty_ai_workbench_block():
    # 關鍵句搬咗去主欄之後，「AI 工作台」block 淨返一個 priority badge，
    # 而嗰個 badge 喺文章頂部 eyebrow 已經有一模一樣嘅（同一個
    # priorityLabel()）——用戶影相問「仲有乜用」。已剷。
    html = (ROOT / "docs/article.html").read_text(encoding="utf-8")
    aside = html[html.index('<aside class="ai">'):]
    # 剝走 HTML 註釋先——解釋點解剷咗嗰段本身就提到「AI 工作台」。
    aside = re.sub(r"<!--.*?-->", "", aside, flags=re.S)
    assert "AI 工作台" not in aside, "空殼 block 應該剷走"
    assert 'id="priorityNote"' not in html
    js = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    assert "priorityNote" not in js, "render 都要一齊剷"


def test_priority_badge_keeps_its_score_tooltip():
    # 剷 block 唔可以蝕咗嗰個計分解釋——移咗去 eyebrow 個 badge。
    js = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    idx = js.index('<span class="priority"')
    span = js[idx:idx + 200]
    assert "title=" in span and "優先度" in span, "eyebrow badge 要有解釋 tooltip"
