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
    assert 'data-sort="ai"' in html
    assert ">AI</button>" in html
    assert 'id="trending-topics"' in html


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
