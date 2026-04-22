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
          querySelectorAll() { return []; }
        }

        const els = new Map();
        for (const id of [
          "theme-toggle", "news-toast", "toast-msg", "toast-refresh", "toast-close",
          "updated", "health-overlay", "health-close", "health-body", "search",
          "filters", "source-filters", "tag-filters", "trending-topics",
          "sort-toggle", "grid", "font-dec", "font-inc",
        ]) {
          els.set(id, new El(id));
        }

        const document = {
          body: new El("body"),
          getElementById: id => els.get(id) || new El(id),
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
          Date, URL, encodeURIComponent, Number, String, Set, Map, RegExp, JSON,
          Fuse: class {
            constructor(items) { this.items = items; }
            search() { return []; }
          },
          fetch: async () => ({
            json: async () => JSON.parse(fs.readFileSync("docs/data/articles.json", "utf8")),
          }),
        };
        context.globalThis = context;

        vm.runInNewContext(fs.readFileSync("docs/js/common.js", "utf8"), context);
        vm.runInNewContext(fs.readFileSync("docs/js/index.js", "utf8"), context);

        setTimeout(() => {
          if (!els.get("grid").innerHTML.includes("class=\\"card")) {
            throw new Error("index did not render article cards");
          }
        }, 0);
        """
    )
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_index_tag_filters_are_scoped_per_category():
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    fn = _extract_js_function(source, "topTagsForCategory")
    js = fn + """
    const articles = [
      { category: "新聞", tags: ["港聞", "交通"] },
      { category: "新聞", tags: ["港聞"] },
      { category: "科技", tags: ["AI"] },
      { category: "科技", tags: ["AI", "晶片"] },
      { category: "科技", tags: ["晶片"] },
    ];
    const news = topTagsForCategory(articles, "新聞");
    const tech = topTagsForCategory(articles, "科技");
    const all = topTagsForCategory(articles, "全部");
    if (JSON.stringify(news) !== JSON.stringify(["港聞"])) throw new Error("bad news tags: " + JSON.stringify(news));
    if (JSON.stringify(tech) !== JSON.stringify(["AI", "晶片"])) throw new Error("bad tech tags: " + JSON.stringify(tech));
    if (!all.includes("港聞") || !all.includes("AI") || !all.includes("晶片")) throw new Error("bad all tags: " + JSON.stringify(all));
    """
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_index_trending_topics_are_scoped_per_category():
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    fn = _extract_js_function(source, "trendingTopicsForCategory")
    js = fn + """
    const topics = [
      { topic: "特朗普", article_ids: ["n1", "i1"], count: 2 },
      { topic: "晶片", article_ids: ["t1", "t2"], count: 2 },
    ];
    const articles = [
      { id: "n1", category: "新聞" },
      { id: "i1", category: "國際" },
      { id: "t1", category: "科技" },
      { id: "t2", category: "科技" },
    ];
    const news = trendingTopicsForCategory(topics, articles, "新聞").map(t => t.topic);
    const tech = trendingTopicsForCategory(topics, articles, "科技").map(t => t.topic);
    if (JSON.stringify(news) !== JSON.stringify(["特朗普"])) throw new Error("bad news topics: " + JSON.stringify(news));
    if (JSON.stringify(tech) !== JSON.stringify(["晶片"])) throw new Error("bad tech topics: " + JSON.stringify(tech));
    """
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_index_has_ai_sort_button():
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    assert 'data-sort="date"' in html
    assert ">最新</button>" in html
    assert 'data-sort="ai"' in html
    assert ">AI</button>" in html
    assert 'id="trending-topics"' in html


def test_index_latest_sort_orders_by_date():
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    funcs = "\n".join(
        _extract_js_function(source, name)
        for name in ["articleTime", "compareByDate", "aiRankScore", "getSorted"]
    )
    js = funcs + """
    let sortMode = "date";
    const articles = [
      { id: "old", date: "2026-04-20T10:00:00+08:00" },
      { id: "new", date: "2026-04-22T10:00:00+08:00" },
      { id: "mid", date: "2026-04-21T10:00:00+08:00" },
    ];
    const ids = getSorted(articles).map(a => a.id);
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


def test_index_compacts_clusters_to_one_representative():
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    funcs = "\n".join(
        _extract_js_function(source, name)
        for name in [
            "articleTime",
            "compareByDate",
            "aiRankScore",
            "getSorted",
            "clusterKey",
            "compactClusters",
        ]
    )
    js = funcs + """
    let sortMode = "date";
    const articles = [
      { id: "cluster-old", cluster_id: "abcdef12", cluster_size: 3, date: "2026-04-20T10:00:00+08:00" },
      { id: "cluster-new", cluster_id: "abcdef12", cluster_size: 3, date: "2026-04-22T10:00:00+08:00" },
      { id: "single", date: "2026-04-21T10:00:00+08:00" },
    ];
    const ids = getSorted(compactClusters(articles)).map(a => a.id);
    if (JSON.stringify(ids) !== JSON.stringify(["cluster-new", "single"])) {
      throw new Error("cluster compaction picked wrong representative: " + JSON.stringify(ids));
    }
    """
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_index_cluster_badge_expands_all_sources():
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    fn = _extract_js_function(source, "filterCluster")
    assert "compactClusters" not in fn
    assert "all.filter(a => a.cluster_id === cid)" in fn


def test_index_cluster_cards_are_stacked_and_click_to_expand():
    html = (ROOT / "docs/index.html").read_text(encoding="utf-8")
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    assert ".card.cluster-stack" in html
    assert ".card.cluster-expanded" in html
    assert 'isClusterStack ? `#cluster-${cid}`' in source
    assert 'isClusterStack ? ` onclick="event.preventDefault();filterCluster' in source
    assert 'isClusterStack ? " · 點擊展開" : ""' in source


def test_index_summary_points_normalise_bullets():
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
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


def test_index_key_fact_items_prioritise_event_entities():
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    fn = _extract_js_function(source, "keyFactItems")
    js = fn + """
    const items = keyFactItems({
      event_type: "事故",
      entities: {
        people: ["張三"],
        companies: ["港鐵"],
        places: ["大埔"],
        dates: ["4月22日"],
        numbers: ["8人"],
      },
    }).map(x => x.label + ":" + x.value);
    if (JSON.stringify(items) !== JSON.stringify(["類型:事故", "人物:張三", "公司:港鐵", "地點:大埔", "日期:4月22日"])) {
      throw new Error("bad key facts: " + JSON.stringify(items));
    }
    """
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_index_key_facts_html_labels_values():
    node = _require_node()
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    js = "\n".join([
        "const esc = s => String(s);",
        _extract_js_function(source, "keyFactItems"),
        _extract_js_function(source, "keyFactsHtml"),
        """
        const html = keyFactsHtml({
          event_type: "政治",
          entities: { places: ["香港"], numbers: ["49國"] },
        });
        if (!html.includes("類型：政治") || !html.includes("地點：香港") || !html.includes("數字：49國")) {
          throw new Error("labels missing: " + html);
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


def test_index_update_timestamp_opens_source_health():
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    listener = source[source.index('document.getElementById("updated").addEventListener("click"'):]
    listener = listener[:listener.index("// ── Search")]
    assert "openHealthModal();" in listener
    assert "checkUpdates();" not in listener


def test_article_page_applies_saved_light_theme():
    node = _require_node()
    source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    fn = _extract_js_function(source, "applySavedTheme")
    js = fn + """
    const classes = new Set();
    const document = {
      body: {
        classList: {
          toggle(name, on) {
            if (on) classes.add(name);
            else classes.delete(name);
          },
        },
      },
      querySelector() {
        return { setAttribute(name, value) { this[name] = value; globalThis.themeColor = value; } };
      },
    };
    const localStorage = { getItem: key => key === "rss_theme" ? "light" : null };
    const window = { matchMedia: () => ({ matches: false }) };
    applySavedTheme();
    if (!classes.has("theme-light") || classes.has("theme-dark")) {
      throw new Error("saved light theme was not applied");
    }
    if (globalThis.themeColor !== "#fafaf8") {
      throw new Error("theme color not updated: " + globalThis.themeColor);
    }
    """
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_article_share_uses_original_source_url():
    source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    assert "let currentSourceUrl" in source
    assert "const url   = currentSourceUrl || location.href;" in source
    assert 'currentSourceUrl = srcUrl !== "#" ? srcUrl : "";' in source


def test_article_fact_items_group_entities():
    node = _require_node()
    source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    fn = _extract_js_function(source, "articleFactItems")
    js = fn + """
    const items = articleFactItems({
      event_type: "財經",
      entities: { companies: ["蘋果"], places: ["美國"], numbers: ["600億美元"] },
    });
    const labels = items.map(x => x.label + ":" + x.value);
    if (JSON.stringify(labels) !== JSON.stringify(["類型:財經", "公司:蘋果", "地點:美國", "數字:600億美元"])) {
      throw new Error("bad article facts: " + JSON.stringify(labels));
    }
    """
    result = subprocess.run(
        [node, "-e", js],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_article_nav_uses_session_context():
    node = _require_node()
    source = (ROOT / "docs/js/article.js").read_text(encoding="utf-8")
    js = "\n".join([
        'const NAV_CONTEXT_KEY = "rss_article_nav_context";',
        _extract_js_function(source, "articleUrl"),
        _extract_js_function(source, "readNavContext"),
        _extract_js_function(source, "setNavLink"),
        _extract_js_function(source, "setupArticleNav"),
        """
        const els = new Map();
        class El {
          constructor(id) {
            this.id = id;
            this.href = "";
            this.attrs = {};
            this.classes = new Set(["disabled"]);
            this.classList = {
              add: name => this.classes.add(name),
              remove: name => this.classes.delete(name),
              contains: name => this.classes.has(name),
            };
          }
          removeAttribute(name) { delete this.attrs[name]; if (name === "href") this.href = ""; }
          setAttribute(name, value) { this.attrs[name] = value; }
        }
        els.set("nav-prev", new El("nav-prev"));
        els.set("nav-next", new El("nav-next"));
        const document = { getElementById: id => els.get(id) };
        const sessionStorage = {
          getItem: key => key === NAV_CONTEXT_KEY ? JSON.stringify({ ids: ["a", "b", "c"] }) : null,
        };
        setupArticleNav("b", [{ id: "x" }, { id: "b" }, { id: "y" }]);
        if (!els.get("nav-prev").href.endsWith("article.html?id=a")) {
          throw new Error("prev link not set: " + els.get("nav-prev").href);
        }
        if (!els.get("nav-next").href.endsWith("article.html?id=c")) {
          throw new Error("next link not set: " + els.get("nav-next").href);
        }
        if (els.get("nav-prev").classes.has("disabled") || els.get("nav-next").classes.has("disabled")) {
          throw new Error("nav links should be enabled");
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
    source = (ROOT / "docs/js/index.js").read_text(encoding="utf-8")
    js = "\n".join([
        _extract_js_function(source, "articleTime"),
        _extract_js_function(source, "aiRankScore"),
        """
        const now = Date.parse("2026-04-21T12:00:00Z");
        const high = aiRankScore({
          score: 9,
          cluster_size: 3,
          date: "2026-04-20T12:00:00Z",
        }, now);
        const lowButFresh = aiRankScore({
          score: 4,
          cluster_size: 1,
          date: "2026-04-21T11:30:00Z",
        }, now);
        const clustered = aiRankScore({
          score: 6,
          cluster_size: 4,
          date: "2026-04-21T10:00:00Z",
        }, now);
        const solo = aiRankScore({
          score: 6,
          cluster_size: 1,
          date: "2026-04-21T10:00:00Z",
        }, now);
        if (high <= lowButFresh) throw new Error("importance should dominate");
        if (clustered <= solo) throw new Error("cluster bonus missing");
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
