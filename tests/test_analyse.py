"""Unit tests for src/analyse.py parsing helpers.

Run:  python -m pytest tests/ -v
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import src.analyse as analyse
from src.analyse import (
    ANALYSIS_VERSION,
    _needs_full_analysis,
    _normalise_entities,
    _normalise_summary,
    _parse_analysis,
    _parse_batch,
)
from src.fetch import _clean_url


# ── _normalise_summary ────────────────────────────────────────────

def test_normalise_from_list():
    out = _normalise_summary(["foo", "bar"])
    assert out == "・foo\n・bar"


def test_normalise_from_list_strips_existing_bullets():
    out = _normalise_summary(["・foo", " ・bar"])
    assert out == "・foo\n・bar"


def test_normalise_list_drops_empty():
    assert _normalise_summary(["foo", "", "  "]) == "・foo"


def test_normalise_already_formatted_passthrough():
    text = "・foo\n・bar"
    assert _normalise_summary(text) == text


def test_normalise_string_with_bullets_no_newlines_split():
    out = _normalise_summary("・foo・bar・baz")
    assert out == "・foo\n・bar\n・baz"


def test_normalise_preserves_interdot_names_in_prose():
    # Must not split interdot names like 奧巴馬・侯賽因 when the text is prose
    # (not a bullet list — doesn't start with ・).
    text = "奧巴馬・侯賽因訪問香港"
    assert _normalise_summary(text) == text


def test_normalise_none_returns_empty():
    assert _normalise_summary(None) == ""


def test_normalise_single_bullet_passthrough():
    # Single ・ at start but only one bullet — should not split.
    assert _normalise_summary("・單一重點") == "・單一重點"


# ── _parse_analysis ──────────────────────────────────────────────

def test_parse_plain_json():
    raw = '{"summary":"・a\\n・b","score":7,"tags":["x","y"],"sentiment":"negative","topic":"test","event_type":"事故","entities":{"people":["張三"],"companies":["港鐵"],"places":["大埔"],"dates":["4月22日"],"numbers":["8人"]}}'
    out = _parse_analysis(raw)
    assert out["summary"] == "・a\n・b"
    assert out["score"] == 7
    assert out["tags"] == ["x", "y"]
    assert out["sentiment"] == "negative"
    assert out["topic"] == "test"
    assert out["event_type"] == "事故"
    assert out["entities"]["people"] == ["張三"]
    assert out["entities"]["companies"] == ["港鐵"]
    assert out["entities"]["places"] == ["大埔"]
    assert out["entities"]["dates"] == ["4月22日"]
    assert out["entities"]["numbers"] == ["8人"]
    assert out["version"] == ANALYSIS_VERSION


def test_parse_strips_markdown_fence():
    raw = '```json\n{"summary":"・a","score":5,"tags":[],"sentiment":"neutral","topic":"x"}\n```'
    out = _parse_analysis(raw)
    assert out is not None
    assert out["score"] == 5


def test_parse_clamps_score_out_of_range():
    raw = '{"summary":"x","score":99,"tags":[],"sentiment":"neutral","topic":""}'
    assert _parse_analysis(raw)["score"] == 10
    raw = '{"summary":"x","score":-5,"tags":[],"sentiment":"neutral","topic":""}'
    assert _parse_analysis(raw)["score"] == 1


def test_parse_score_string_falls_back_to_5():
    raw = '{"summary":"x","score":"not-a-number","tags":[],"sentiment":"neutral","topic":""}'
    assert _parse_analysis(raw)["score"] == 5


def test_parse_invalid_sentiment_defaults_neutral():
    raw = '{"summary":"x","score":5,"tags":[],"sentiment":"nonsense","topic":""}'
    assert _parse_analysis(raw)["sentiment"] == "neutral"


def test_parse_tags_as_comma_string():
    raw = '{"summary":"x","score":5,"tags":"政治,經濟、社會","sentiment":"neutral","topic":""}'
    assert _parse_analysis(raw)["tags"] == ["政治", "經濟", "社會"]


def test_parse_tags_capped_at_three():
    raw = '{"summary":"x","score":5,"tags":["a","b","c","d","e"],"sentiment":"neutral","topic":""}'
    assert _parse_analysis(raw)["tags"] == ["a", "b", "c"]


def test_parse_strips_hash_from_tags():
    raw = '{"summary":"x","score":5,"tags":["#政治","經濟"],"sentiment":"neutral","topic":""}'
    assert _parse_analysis(raw)["tags"] == ["政治", "經濟"]


def test_parse_topic_truncated():
    raw = '{"summary":"x","score":5,"tags":[],"sentiment":"neutral","topic":"' + "長" * 50 + '"}'
    assert len(_parse_analysis(raw)["topic"]) == 20


def test_parse_summary_as_list():
    raw = '{"summary":["重點一","重點二"],"score":5,"tags":[],"sentiment":"neutral","topic":""}'
    out = _parse_analysis(raw)
    assert out["summary"] == "・重點一\n・重點二"


def test_normalise_entities_accepts_strings_and_caps_lists():
    out = _normalise_entities({
        "people": "張三、李四",
        "companies": ["港鐵", "港鐵", "政府", "公司A", "公司B"],
        "places": None,
        "dates": 123,
        "numbers": ["123456789012345678901234567890"],
    })
    assert out["people"] == ["張三", "李四"]
    assert out["companies"] == ["港鐵", "政府", "公司A", "公司B"]
    assert out["places"] == []
    assert out["dates"] == []
    assert out["numbers"] == ["123456789012345678901234"]


def test_parse_garbage_returns_none():
    assert _parse_analysis("not json") is None
    assert _parse_analysis("") is None


def test_parse_embedded_json_in_prose():
    # Model sometimes wraps JSON with chatter — we extract the {...} block.
    raw = '分析結果如下：{"summary":"x","score":5,"tags":[],"sentiment":"neutral","topic":""} 希望對你有幫助'
    assert _parse_analysis(raw) is not None


# ── _needs_full_analysis ─────────────────────────────────────────

def test_needs_when_score_missing():
    assert _needs_full_analysis({"summary": "x", "score": None}) is True


def test_needs_when_version_stale():
    assert _needs_full_analysis({
        "summary": "x", "score": 5, "version": "p-deadbeef"
    }) is True


def test_needs_when_version_missing():
    assert _needs_full_analysis({"summary": "x", "score": 5}) is True


def test_no_need_when_current_version():
    assert _needs_full_analysis({
        "summary": "x", "score": 5, "version": ANALYSIS_VERSION
    }) is False


def test_needs_when_summary_is_list_repr():
    # Legacy malformed entry: AI returned array, str() got cached.
    assert _needs_full_analysis({
        "summary": "['重點一', '重點二']", "score": 5, "version": ANALYSIS_VERSION
    }) is True


# ── _parse_batch ─────────────────────────────────────────────────

def test_parse_batch_array_matches_expected():
    raw = (
        '[{"summary":"・a","score":5,"tags":["x"],"sentiment":"neutral","topic":""},'
        '{"summary":"・b","score":8,"tags":[],"sentiment":"negative","topic":""}]'
    )
    out = _parse_batch(raw, 2)
    assert out is not None
    assert len(out) == 2
    assert out[0]["summary"] == "・a"
    assert out[1]["score"] == 8


def test_parse_batch_length_mismatch_returns_none():
    raw = '[{"summary":"x","score":5,"tags":[],"sentiment":"neutral","topic":""}]'
    assert _parse_batch(raw, 2) is None


def test_parse_batch_partial_returns_list_with_none():
    # Shape matches (length 2) but second item is not an object.
    raw = (
        '[{"summary":"x","score":5,"tags":[],"sentiment":"neutral","topic":""},'
        '"garbage"]'
    )
    out = _parse_batch(raw, 2)
    assert out is not None and len(out) == 2
    assert out[0] is not None and out[1] is None


def test_parse_batch_single_malformed_item_returns_none_slot():
    out = _parse_batch('["garbage"]', 1)
    assert out == [None]


def test_analyse_one_treats_none_slot_as_parse_failure(monkeypatch):
    async def fake_post_messages(*args, **kwargs):
        return '["garbage"]', {}, 200

    async def fake_sleep(_delay):
        return None

    monkeypatch.setattr(analyse, "_post_messages", fake_post_messages)
    monkeypatch.setattr(analyse.asyncio, "sleep", fake_sleep)

    article = {
        "id": "bad",
        "title": "Bad parse",
        "url": "https://example.com/bad",
        "content": "<p>text</p>",
    }
    asyncio.run(analyse._analyse_one(
        session=None,
        article=article,
        sem=asyncio.Semaphore(1),
        cache={},
        save_lock=asyncio.Lock(),
        counter=[0],
    ))

    assert "summary" not in article


def test_parse_batch_single_accepts_bare_object():
    raw = '{"summary":"x","score":5,"tags":[],"sentiment":"neutral","topic":""}'
    out = _parse_batch(raw, 1)
    assert out is not None and len(out) == 1


def test_parse_batch_strips_fence():
    raw = '```json\n[{"summary":"x","score":5,"tags":[],"sentiment":"neutral","topic":""}]\n```'
    assert _parse_batch(raw, 1) is not None


# ── _clean_url ────────────────────────────────────────────────────

def test_clean_url_strips_trailing_quote_garbage():
    # 明報 娛樂 feeds emit <link>…</link> with " target="blank" embedded.
    dirty = 'https://example.com/a/b" target="blank"'
    assert _clean_url(dirty) == "https://example.com/a/b"


def test_clean_url_passthrough_for_good_url():
    assert _clean_url("https://example.com/x") == "https://example.com/x"


def test_clean_url_empty():
    assert _clean_url("") == ""
    assert _clean_url(None) == ""
