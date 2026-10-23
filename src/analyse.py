import asyncio
import hashlib
import json
import os
import re
import time
from collections import defaultdict, deque
from itertools import zip_longest
from pathlib import Path

import aiohttp
from bs4 import BeautifulSoup
from src.event_dates import EVENT_DATE_VERSION, validate_event
from src.article_evidence import validate_summary_evidence, body_text, normalise_text, verified_quote
from src.enrichment_budget import legacy_source_limit
from src.summary_contract import SUMMARY_FORMAT, normalise_points, validate_record
from src.analysis_input import input_hash, source_text
from src.analysis_statistics import CURRENT, RESPONSE_METADATA, count
from src import analysis_diagnostics as diagnostic

from src.minimax_client import (
    MINIMAX_API_KEY,
    MINIMAX_MODEL,
    post_messages,
    should_retry as _should_retry,
)

# Token Plan regularly rate-limits at 10 concurrent. Dropped to 5 after repeated
# 2062 errors caused majority of analyses to fail in a single build run.
ANALYSE_CONCURRENCY  = 5
BATCH_SIZE           = 5    # articles per batched API call — trades per-request overhead
                            # (incl. system prompt tokens) for a single larger call
MAX_ATTEMPTS         = 4    # total tries per call (incl. parse-fail retries)
MAX_BACKOFF_BUDGET   = 60.0 # hard cap on cumulative wait per call (seconds)
SAVE_CACHE_EVERY     = 20   # incremental cache flush so crash mid-run is not fatal

CACHE_PATH = Path(__file__).parent.parent / "docs" / "data" / "analyses.json"

SYSTEM_PROMPT = (
    "你係一個新聞分析助手。"
    "輸出一個 JSON 陣列，每篇新聞對應陣列內一個 object，按輸入編號順序排列，"
    "唔好有任何其他文字、解釋、markdown 或思考過程。"
    "陣列長度必須等於輸入新聞數量。\n"
    "每個 object 格式：\n"
    '{"idx":整數（對應輸入嘅「第X篇」入面嘅X，必須逐個對應、唔可以漏或者重複，'
    '呢個field唔理你輸出嘅array順序係點，我哋都靠佢核對邊個object屬於邊篇新聞）,'
    '"summary_points":[{"sentences":["第一句完整自然句子。","第二句完整自然句子。"],'
    '"quotes":["支持第一句嘅內文逐字引句","支持第二句嘅內文逐字引句"]}],'
    '"summary_limited":false（目標正好五點，每點兩句有實質資料嘅完整自然句子；每句交代主體、事實同必要背景，'
    '唔好用標語或十字片語。只可以用輸入正文直接支持嘅事實，每句附一條最短足夠嘅逐字引句；'
    '禁止外加知識、推測、重複改寫同湊數。來源不足時設true，少寫點數或只保留有根據嘅一句；完全冇資料可用[]。'
    '句子保留句號；人物名字中間嘅・唔係分點。無須再輸出summary字串或重複summary_evidence）,'
    '"score":整數1到10（10=突發重大，5=一般新聞，1=普通資訊）,'
    '"tags":["標籤1","標籤2"]（最多3個中文標籤，唔帶#）,'
    '"sentiment":"positive"或"negative"或"neutral",'
    '"headline_fit":整數0到10（標題同內文相符程度：10=完全相符，5=標題有啲誇大或斷章取義，0=標題黨、內文完全唔支持標題）,'
    '"topic":"標準化話題名稱，唔超過10字",'
    '"event_type":"事件類型，2至6字，例如事故/政治/財經/天氣/娛樂/科技/法庭",'
    '"entities":{"people":["最多2個人物"],"companies":["最多2個公司/機構"],"places":["最多2個地點"],"dates":["最多2個日期"],"numbers":["最多2個關鍵數字"]},'
    '"key_sentences":["原文逐字摘錄最關鍵嘅 3 至 5 句句子（必須完全一致，唔好改寫，每句 10-80 字）"],'
    '"upcoming_events":[{"date":"YYYY-MM-DD或null","date_expression":"原文日期字眼",'
    '"evidence":"逐字摘錄包括日期同事件嘅原文句子","title":"短描述，唔超過20字"}]'
    '（最多2個；日期相對輸入嘅出版日期，以香港時間計算；月底、年初等唔可以猜某一日，date用null；無就[]）}'
)

# Derive version from the prompt hash so the cache auto-invalidates whenever
# SYSTEM_PROMPT changes — no manual bump needed.
ANALYSIS_VERSION = "p-" + hashlib.sha256((SYSTEM_PROMPT + MINIMAX_MODEL + EVENT_DATE_VERSION + str(SUMMARY_FORMAT)).encode("utf-8")).hexdigest()[:12]

_BAD_SUMMARY_PHRASES = (
    "單一字串",
    "單字串",        # abbreviated variant
    "非array",
    "每點用",
    "每點之間用換行符",
    "唔超過10個字",
    "唔超過10字",    # abbreviated variant
    "至8重點",       # "5至8重點" fragment
    "5至8個重點",
)


def load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def save_cache(cache: dict):
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(CACHE_PATH.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, CACHE_PATH)


# Cap each article body fed into the analyse prompt. Five articles per batch
# at this ceiling keeps the user-message under ~12k tokens, well below MiniMax's
# context budget after SYSTEM_PROMPT + JSON-schema response is accounted for.
_MAX_PROMPT_CHARS = 2000
SUMMARY_FIELDS = ('summary_points', 'summary_format', 'summary_status')
ANALYSIS_FIELDS = ('summary', 'summary_evidence', 'score', 'tags', 'sentiment', 'headline_fit',
                   'topic', 'event_type', 'entities', 'key_sentences', 'upcoming_events',
                   'analysis_input_hash', 'analysis_version', *SUMMARY_FIELDS)


def _retain_supported_points(record):
    if record.get('summary_format') != SUMMARY_FORMAT:
        return
    supported = {entry['point']: entry for entry in record.get('summary_evidence', [])}
    original = record['summary_points']
    kept = [point for point in original if ''.join(point) in supported]
    record['summary_points'] = kept
    record['summary'] = '\n'.join('・' + ''.join(point) for point in kept)
    record['summary_evidence'] = [supported[''.join(point)] for point in kept]
    if len(kept) != len(original) or len(kept) < 5 or any(len(point) != 2 for point in kept):
        record['summary_status'] = 'limited'
    validate_record(record)


def _same_cached_input(cached, article):
    if cached.get('summary_format') == SUMMARY_FORMAT:
        try:
            validate_record(cached)
        except (ValueError, TypeError):
            return False
    version = cached.get('version', cached.get('analysis_version'))
    digest = cached.get('input_hash', cached.get('analysis_input_hash'))
    return (isinstance(version, str) and re.fullmatch(r'p-[a-f0-9]{8,64}', version) is not None
            and digest == analysis_input_hash(article, version=version)
            and (bool(cached.get('summary')) or cached.get('summary_format') == SUMMARY_FORMAT
                 and cached.get('summary_status') == 'limited')
            and not looks_like_prompt_schema_summary(cached.get('summary', ''))
            and _summary_looks_acceptable(cached.get('summary', '')))


def _published_cache_record(article):
    """Recover only input-bound analysis fields, never article/body metadata."""
    cached = {field: article[field] for field in ANALYSIS_FIELDS if field in article}
    cached['input_hash'] = cached.pop('analysis_input_hash', None)
    cached['version'] = cached.pop('analysis_version', None)
    return cached if _same_cached_input(cached, article) else None


def _restore_cached(article, cached):
    """Reuse only an unchanged supplied input; prompt migration never deletes it."""
    for field in ANALYSIS_FIELDS:
        if field in cached:
            article[field] = cached[field]
    article['analysis_input_hash'] = cached.get('input_hash', cached.get('analysis_input_hash'))
    article['analysis_version'] = cached.get('version', cached.get('analysis_version'))
    article['analysis_status'] = 'available'
    if article.get('summary_format') != SUMMARY_FORMAT:
        article['summary_status'] = 'legacy'
    article['summary_evidence'] = validate_summary_evidence(article, cached.get('summary_evidence'))
    article['key_sentences'] = _normalise_key_sentences(cached.get('key_sentences'), article)
    article['key_sentences_content_hash'] = hashlib.sha256((article.get('content') or '').encode('utf-8')).hexdigest()


def _extract_text(html_content: str) -> str:
    # Inline tags do not insert spaces into the displayed sentence.
    return normalise_text(body_text(html_content))[:_MAX_PROMPT_CHARS]


def _normalise_summary(raw) -> str:
    """Normalise AI-returned summary into newline-separated ・bullets.
    Handles: list of strings, single string with ・ delimiters missing newlines."""
    if isinstance(raw, list):
        items = [str(x).strip().lstrip("・ ").strip() for x in raw]
        return "\n".join("・" + i for i in items if i)
    text = str(raw or "").strip()
    # MiniMax 偶爾會 return literal "\n"（backslash + n 兩個 char）而唔係 real
    # newline，frontend split-on-newline 就 fail。Normalize 番做真 newline 先
    # 至落到下面 ・-bullet detection。
    text = text.replace("\\n", "\n")
    if not text or "\n" in text:
        return text
    # No newlines: only split on ・ when it's clearly a bullet list (starts
    # with ・) — avoids breaking interdot names like 奧巴馬・侯賽因 in prose.
    if text.startswith("・"):
        return re.sub(r"\s+・", "\n・", text)
    return text


def looks_like_prompt_schema_summary(summary: str) -> bool:
    """Detect when the model echoed the prompt schema instead of a real summary.
    Shared with build.py's fallback restorer so both sides stay in sync."""
    text = str(summary or "")
    hits = sum(1 for phrase in _BAD_SUMMARY_PHRASES if phrase in text)
    return hits >= 2


# Length gate keeps the prose detector from tripping on short placeholders
# (e.g. test stubs, ad-hoc "x" summaries). Real prose summaries that escape
# the schema run well past this — the HP BIOS case that motivated this was
# 116 chars on a single line.
_PROSE_REJECT_MIN_CHARS = 40


def _summary_looks_acceptable(summary: str) -> bool:
    """Return False for long-form prose with no ・bullets and no newlines —
    i.e. summaries that violate the ・-bullet schema SYSTEM_PROMPT demands.
    Caller treats False the same as a parse failure so the retry path runs."""
    text = str(summary or "")
    if len(text) < _PROSE_REJECT_MIN_CHARS:
        return True
    return "・" in text or "\n" in text


def _normalise_string_list(raw, *, limit: int = 4, max_len: int = 24) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [x for x in re.split(r"[,，、\n]+", raw) if x.strip()]
    elif not isinstance(raw, list):
        return []

    out = []
    seen = set()
    for item in raw:
        text = str(item or "").strip().lstrip("#").strip()
        if not text:
            continue
        text = re.sub(r"\s+", " ", text)[:max_len]
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _normalise_entities(raw) -> dict:
    if not isinstance(raw, dict):
        raw = {}
    return {
        "people":    _normalise_string_list(raw.get("people"), limit=2),
        "companies": _normalise_string_list(raw.get("companies"), limit=2),
        "places":    _normalise_string_list(raw.get("places"), limit=2),
        "dates":     _normalise_string_list(raw.get("dates"), limit=2),
        "numbers":   _normalise_string_list(raw.get("numbers"), limit=2),
    }


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalise_key_sentences(raw, article=None) -> list[str]:
    """Verbatim quotes used by the article view to wrap <mark> highlights.
    Reject anything that's clearly not a real sentence (too short, too long,
    or contains line breaks — line breaks would never survive a verbatim
    match against a single text node)."""
    if raw is None:
        return []
    if isinstance(raw, str):
        raw = [raw]
    elif not isinstance(raw, list):
        return []
    out, seen = [], set()
    for item in raw:
        text = re.sub(r"\s+", " ", str(item or "")).strip().strip('"\u201c\u201d「」『』')
        if not (8 <= len(text) <= 120):
            continue
        if article is not None and not verified_quote(text, article, limit=120):
            continue
        if text in seen:
            continue
        seen.add(text)
        out.append(text)
        if len(out) >= 5:
            break
    return out


def _normalise_upcoming_events(raw) -> list[dict]:
    """Future-dated events extracted from the article body.
    Drops entries with a malformed date or empty title."""
    if not isinstance(raw, list):
        return []
    out = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or "").strip()
        title = re.sub(r"\s+", " ", str(item.get("title") or "")).strip()
        evidence = str(item.get("evidence") or "").strip()
        expression = str(item.get("date_expression") or "").strip()
        if not title or (not _DATE_RE.match(date) and not (evidence and expression)):
            continue
        record = {"date": date if _DATE_RE.match(date) else None, "title": title[:30]}
        if evidence and expression:
            record.update(evidence=evidence[:500], date_expression=expression[:80])
        out.append(record)
        if len(out) >= 2:
            break
    return out


def _normalise_parsed(data: dict, *, require_rich=False) -> dict | None:
    """Coerce a parsed JSON object into our canonical analysis dict."""
    if not isinstance(data, dict):
        count('validation_rejections', 'not_object')
        diagnostic.contract_rejection('object_type')
        return None
    try:
        score_raw = data.get("score")
        try:
            score = max(1, min(10, int(score_raw)))
        except (TypeError, ValueError):
            score = 5
        sentiment = str(data.get("sentiment", "neutral")).lower()
        if sentiment not in ("positive", "negative", "neutral"):
            sentiment = "neutral"
        tags_raw = data.get("tags") or []
        if isinstance(tags_raw, str):
            # Model occasionally returns "a,b,c" instead of ["a","b","c"]
            tags_raw = [t for t in re.split(r"[,，、\s]+", tags_raw) if t]
        elif not isinstance(tags_raw, list):
            tags_raw = []
        rich = normalise_points(data['summary_points'], data.get('summary_limited', False)) if 'summary_points' in data else None
        if require_rich and rich is None:
            count('validation_rejections', 'missing_rich_points')
            diagnostic.contract_rejection('missing_rich_points')
            return None
        summary = rich['summary'] if rich else _normalise_summary(data.get("summary"))
        if looks_like_prompt_schema_summary(summary):
            count('validation_rejections', 'prompt_schema_echo')
            diagnostic.contract_rejection('schema_echo')
            return None
        if not _summary_looks_acceptable(summary):
            count('validation_rejections', 'summary_shape')
            diagnostic.contract_rejection('summary_shape')
            return None
        # headline_fit 係 optional——舊 cache entry / 模型漏答就係 None，
        # 前端 None 唔顯示 badge。
        try:
            headline_fit = max(0, min(10, int(data.get("headline_fit"))))
        except (TypeError, ValueError):
            headline_fit = None
        result = {
            "summary":   summary,
            "score":     score,
            "tags":      [str(t).strip().lstrip("#") for t in tags_raw[:3] if str(t).strip()],
            "sentiment": sentiment,
            "headline_fit": headline_fit,
            "topic":     str(data.get("topic", "")).strip()[:20],
            "event_type": str(data.get("event_type", "")).strip()[:12],
            "entities":  _normalise_entities(data.get("entities")),
            "key_sentences":   _normalise_key_sentences(data.get("key_sentences")),
            "summary_evidence": data.get("summary_evidence") if isinstance(data.get("summary_evidence"), list) else [],
            "upcoming_events": _normalise_upcoming_events(data.get("upcoming_events")),
            "version":   ANALYSIS_VERSION,
        }
        if rich:
            result.update(rich)
        return result
    except Exception as exc:
        reasons = {
            'five points required unless source is limited': 'point_count',
            'invalid summary point count': 'point_count',
            'two sentences required unless source is limited': 'sentence_count',
            'summary needs a complete sentence': 'sentence_incomplete',
            'one sentence per structured slot': 'sentence_boundaries',
            'sentence must not contain a bullet marker': 'sentence_bullet',
            'repeated summary sentence': 'duplicate_sentence',
            'every sentence needs source support': 'quote_count',
            'invalid source quote': 'quote_length',
            'structured sentences required': 'sentence_structure',
        }
        count('validation_rejections', reasons.get(str(exc), 'malformed_object'))
        diagnostic.contract_rejection(getattr(exc, 'code', 'malformed_object'))
        return None


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^\s*```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$",          "", text)
    return text


MAX_ANALYSIS_RESPONSE_CHARS = 128 * 1024  # parser resource guard, not a token-budget change


def _decode_document(raw):
    if not isinstance(raw, str): return None, 'json_decode'
    if len(raw) > MAX_ANALYSIS_RESPONSE_CHARS: return None, 'response_excess'
    text = _strip_fences(raw)
    try:
        return json.loads(text), None
    except (ValueError, RecursionError):
        # Preserve harmless prose/fence wrappers, but never scan inside a bad
        # outer document for a valid nested array or salvage truncated output.
        decoder = json.JSONDecoder()
        try:
            value, end = decoder.raw_decode(text)
        except (ValueError, RecursionError):
            if text.startswith(('[', '{', '"')): return None, 'json_decode'
            start = re.search(r'[\[{]', text)
            if start is None: return None, 'missing_document'
            try:
                value, end = decoder.raw_decode(text, start.start())
            except (ValueError, RecursionError):
                return None, 'json_decode'
        suffix = text[end:].strip()
        if suffix:
            try:
                decoder.raw_decode(suffix)
            except (ValueError, RecursionError):
                pass
            else:
                return None, 'extra_document'
        if re.search(r'[\[\]{}]', text[end:]): return None, 'extra_document'
        return value, None


def _parse_analysis(raw: str, *, require_rich=False) -> dict | None:
    """Legacy single-record reader, separate from generation index binding."""
    value, error = _decode_document(raw)
    if error: return None
    if isinstance(value, list) and len(value) == 1: value = value[0]
    return _normalise_parsed(value, require_rich=require_rich) if isinstance(value, dict) else None


def _article_index(value, expected):
    if type(value) is int:
        idx = value
    elif isinstance(value, str) and len(value.strip()) <= 8 and re.fullmatch(r'[+-]?[0-9]+', value.strip()):
        idx = int(value)
    else:
        return None
    return idx if 1 <= idx <= expected else None


def _parse_batch(raw: str, expected: int, *, require_rich=False) -> list[dict | None] | None:
    """Parse a JSON array of analyses. Returns None on total shape mismatch
    so the caller retries or falls back. When the array shape matches but
    individual items are malformed, returns a list of same length with
    None at the failed slots — caller applies successes, fills failures
    via per-article fallback."""
    if type(expected) is not int or not 1 <= expected <= BATCH_SIZE:
        count('validation_rejections', 'batch_shape_or_json')
        count('results', 'whole_batch_rejections')
        diagnostic.envelope_rejection('batch_length')
        return None
    value, error = _decode_document(raw)
    if not error:
        diagnostic.root_shape(value, expected)
        if isinstance(value, dict) and expected == 1:
            bare = True
            arr = [value]
        elif isinstance(value, list):
            bare = False
            arr = value
            if len(arr) != expected: error = 'batch_length'
        else:
            error = 'root_type'
    if error:
        count('validation_rejections', 'batch_shape_or_json')
        count('results', 'whole_batch_rejections')
        diagnostic.envelope_rejection(error)
        return None
    by_idx, seen = {}, set()
    binding_failed = False
    for obj in arr:
        if not isinstance(obj, dict):
            count('validation_rejections', 'not_object')
            count('results', 'rejected_slots')
            diagnostic.binding_rejection('object_type')
            binding_failed = True
            continue
        diagnostic.object_shape(obj)
        # A bare single-object response without idx was already supported.
        # A supplied wrong idx must never bypass the array-path checks.
        idx = 1 if bare and 'idx' not in obj else _article_index(obj.get('idx'), expected)
        if idx is None or idx in seen:
            missing = 'idx' not in obj
            reason = 'missing_index' if missing else 'duplicate_index' if idx in seen else 'invalid_index'
            count('validation_rejections', 'missing_index' if missing else 'invalid_or_duplicate_index')
            count('results', 'rejected_slots')
            diagnostic.binding_rejection(reason)
            binding_failed = True
            continue
        seen.add(idx)
        observation = diagnostic.ATTEMPT.get()
        if observation is not None: observation.bound_index = idx
        try:
            normalised = _normalise_parsed(obj, require_rich=require_rich)
        finally:
            if observation is not None: observation.bound_index = None
        if normalised:
            by_idx[idx] = normalised
        else:
            count('results', 'rejected_slots')
    diagnostic.request_outcome('accepted' if len(by_idx) == expected else 'partial' if by_idx else
                               'binding_rejected' if binding_failed else 'contract_rejected')
    return [by_idx.get(i) for i in range(1, expected + 1)]


def _needs_full_analysis(cached: dict, article: dict | None = None) -> bool:
    """Return True if cached entry is stale/malformed and should be re-analysed."""
    if not isinstance(cached, dict) or cached.get("score") is None:
        return True
    if article is not None and cached.get("input_hash") != analysis_input_hash(article):
        return True
    # Auto-invalidate when the prompt hash embedded in the cache entry no
    # longer matches the current ANALYSIS_VERSION hash.
    if cached.get("version") != ANALYSIS_VERSION:
        return True
    if cached.get('summary_format') != SUMMARY_FORMAT:
        return True
    try:
        validate_record(cached)
    except (ValueError, TypeError):
        return True
    # Summary was stored as Python list repr (AI returned array, str()-ified)
    summary = cached.get("summary", "") or ""
    if summary.startswith("[") and summary.endswith("]") and "', '" in summary:
        return True
    if looks_like_prompt_schema_summary(summary):
        return True
    if not _summary_looks_acceptable(summary):
        return True
    return False


def _article_text(a: dict) -> str:
    """Choose the best available text source for analysis."""
    return source_text(a)


def analysis_input_hash(article: dict, *, version=None) -> str:
    return input_hash(article, version or ANALYSIS_VERSION)


def _format_article(article: dict, idx: int) -> str:
    return (f"### 第 {idx} 篇\n來源：{article.get('source', '')}\n"
            f"出版日期：{article.get('date', '')}（相對日期用香港時間）\n"
            f"標題：{article.get('title', '')}\n內容：{_article_text(article)}")


def _validated_events(article, events):
    return [result for event in events or []
            if (result := validate_event(event, title=article.get("title", ""),
                                         text=_article_text(article), published=article.get("date")))]


async def _post_messages(
    session:    aiohttp.ClientSession,
    user_text:  str,
    max_tokens: int,
    timeout:    float,
) -> tuple[str, dict, int]:
    """Per-article wrapper that pins the system prompt to SYSTEM_PROMPT.
    Returns (raw_text, error_dict, http_status)."""
    # thinking disabled: analysis is structured-output JSON — M3 thinking
    # tokens would eat into max_tokens and truncate the array. M2.7 accepts
    # the param and ignores it (probed 2026-06-10).
    return await post_messages(
        session,
        system=SYSTEM_PROMPT,
        user_text=user_text,
        max_tokens=max_tokens,
        timeout=timeout,
        thinking={"type": "disabled"},
        metadata=RESPONSE_METADATA.get(),
    )


async def _observed_post(session, articles, user_text, max_tokens, timeout, kind):
    stats = CURRENT.get()
    metadata = {}
    token = RESPONSE_METADATA.set(metadata)
    started = time.monotonic()
    if stats is not None:
        stats.started(articles, kind)
    try:
        result = await _post_messages(session, user_text, max_tokens, timeout)
        metadata.setdefault('http_status', result[2])
        diagnostic.returned(result[0], result[1], metadata)
        count('requests', 'returned')
        return result
    except asyncio.CancelledError:
        count('requests', 'cancelled')
        diagnostic.request_outcome('cancelled')
        raise
    except TimeoutError:
        count('requests', 'timed_out')
        diagnostic.request_outcome('request_timeout')
        raise
    except Exception:
        count('requests', 'transport_error')
        diagnostic.request_outcome('transport_error')
        raise
    finally:
        if stats is not None:
            stats.finished(metadata, time.monotonic() - started)
        RESPONSE_METADATA.reset(token)


async def _apply_results(
    batch:     list,
    parsed:    list,
    cache:     dict,
    save_lock: asyncio.Lock,
    counter:   list,
):
    """Write parsed analyses back to articles + cache, save lazily.
    asyncio is single-threaded so the mutations below are atomic w.r.t.
    other coroutines; the lock only serializes the sync disk write."""
    for a, p in zip(batch, parsed):
        original_points = len(p.get('summary_points') or [])
        p = dict(p, input_hash=analysis_input_hash(a))
        p['key_sentences'] = _normalise_key_sentences(p.get('key_sentences'), a)
        p['key_sentences_content_hash'] = hashlib.sha256((a.get('content') or '').encode('utf-8')).hexdigest()
        p["upcoming_events"] = _validated_events(a, p.get("upcoming_events"))
        if p.get('summary_format') == SUMMARY_FORMAT:
            # Only quotes actually supplied to this generation may support it.
            supplied = _article_text(a)
            p['summary_evidence'] = [e for e in p['summary_evidence']
                                     if all(q in supplied for q in e.get('quotes', []))]
        p["summary_evidence"] = validate_summary_evidence({**a, "summary": p["summary"]}, p.get("summary_evidence"))
        _retain_supported_points(p)
        count('results', 'unsupported_points_removed', original_points - len(p.get('summary_points') or []))
        if original_points > len(p.get('summary_points') or []): diagnostic.support_removed(a['id'])
        a["analysis_input_hash"] = p["input_hash"]
        a["analysis_version"] = ANALYSIS_VERSION
        a['analysis_status'] = 'available'
        a.pop('_paid_enrichment_deferred', None)
        a["summary"]   = p["summary"]
        a["summary_evidence"] = p["summary_evidence"]
        for field in SUMMARY_FIELDS:
            if field in p:
                a[field] = p[field]
            else:
                a.pop(field, None)
        a["score"]     = p["score"]
        a["tags"]      = p["tags"]
        a["sentiment"] = p["sentiment"]
        a["headline_fit"] = p.get("headline_fit")
        a["topic"]     = p["topic"]
        a["event_type"] = p.get("event_type", "")
        a["entities"]   = p.get("entities", _normalise_entities({}))
        a["key_sentences"]   = p.get("key_sentences", [])
        a["upcoming_events"] = p.get("upcoming_events", [])
        cache[a["id"]] = p
        stats = CURRENT.get()
        if stats is not None and a['id'] not in stats.applied_ids:
            stats.applied_ids.add(a['id'])
            count('results', 'applied')
            count('results', 'with_summary' if a.get('summary') else 'limited_empty')
    prev = counter[0]
    counter[0] += len(batch)
    if counter[0] // SAVE_CACHE_EVERY > prev // SAVE_CACHE_EVERY:
        async with save_lock:
            save_cache(cache)


async def _analyse_one(
    session:    aiohttp.ClientSession,
    article:    dict,
    sem:        asyncio.Semaphore,
    cache:      dict,
    save_lock:  asyncio.Lock,
    counter:    list,
) -> None:
    """Per-article fallback when a batch fails. Caller must ensure the
    article actually needs analysis (not cached)."""
    title = article.get("title", "")
    text  = _article_text(article)
    if not text.strip():
        return
    user_content = "分析以下 1 篇新聞，返回長度 = 1 嘅 JSON 陣列：\n\n" + _format_article(article, 1)
    async with sem:
        total_waited = 0.0
        for attempt in range(MAX_ATTEMPTS):
            with diagnostic.observe_attempt([article], 'fallback'):
                try:
                    raw, err, status = await _observed_post(session, [article], user_content, 1200, 30, 'fallback')
                    if _should_retry(err, status) and attempt < MAX_ATTEMPTS - 1:
                        delay = min(2 ** (attempt + 2), MAX_BACKOFF_BUDGET - total_waited)
                        if delay <= 0:
                            break
                        await asyncio.sleep(delay)
                        total_waited += delay
                        continue
                    if raw:
                        parsed = _parse_batch(raw, 1, require_rich=True)
                        if parsed and parsed[0]:
                            await _apply_results([article], parsed, cache, save_lock, counter)
                            return
                        if attempt < MAX_ATTEMPTS - 1:
                            delay = min(2 ** attempt, MAX_BACKOFF_BUDGET - total_waited)
                            if delay > 0:
                                await asyncio.sleep(delay)
                                total_waited += delay
                            continue
                        print('[WARN] analyse item rejected after bounded attempts')
                        return
                    if err:
                        print(f'[WARN] analyse item request failed (status {status})')
                    else:
                        print(f'[WARN] analyse item empty response (status {status})')
                    return
                except Exception as exc:
                    if attempt == MAX_ATTEMPTS - 1:
                        print(f'[WARN] analyse item request failed ({type(exc).__name__})')
                        return
                    delay = min(2 ** attempt, MAX_BACKOFF_BUDGET - total_waited)
                    if delay <= 0:
                        return
                    await asyncio.sleep(delay)
                    total_waited += delay


async def _analyse_batch(
    session:   aiohttp.ClientSession,
    batch:     list,
    sem:       asyncio.Semaphore,
    cache:     dict,
    save_lock: asyncio.Lock,
    counter:   list,
) -> None:
    """Analyse up to BATCH_SIZE articles in a single API call.
    Falls back to per-article on batch-level failure."""
    if not batch:
        return

    parts = []
    for i, a in enumerate(batch, 1):
        parts.append(_format_article(a, i))
    user_content = (
        f"分析以下 {len(batch)} 篇新聞，返回長度 = {len(batch)} 嘅 JSON 陣列：\n\n"
        + "\n\n".join(parts)
    )
    # Evidence quotes add output beyond the legacy summary/key-sentence schema.
    # Keep a bounded budget large enough not to truncate verified support arrays.
    # Timeout scales with batch size.
    max_tokens = min(6500, 1100 * len(batch) + 300)
    timeout    = 30 + 10 * len(batch)

    batch_ok = False
    applied_ids: set[str] = set()
    async with sem:
        total_waited = 0.0
        for attempt in range(MAX_ATTEMPTS):
            with diagnostic.observe_attempt(batch, 'batch'):
                try:
                    raw, err, status = await _observed_post(session, batch, user_content, max_tokens, timeout, 'batch')
                    if _should_retry(err, status) and attempt < MAX_ATTEMPTS - 1:
                        delay = min(2 ** (attempt + 2), MAX_BACKOFF_BUDGET - total_waited)
                        if delay <= 0:
                            break
                        await asyncio.sleep(delay)
                        total_waited += delay
                        continue
                    if raw:
                        parsed = _parse_batch(raw, len(batch), require_rich=True)
                        if parsed:
                            # parsed has same length as batch; items may be None
                            # when a single element was malformed — apply the
                            # good ones and let per-article fallback fill gaps.
                            ok_arts = [a for a, p in zip(batch, parsed) if p]
                            ok_parsed = [p for p in parsed if p]
                            if ok_arts:
                                await _apply_results(ok_arts, ok_parsed, cache, save_lock, counter)
                                applied_ids.update(a["id"] for a in ok_arts)
                            if len(ok_arts) == len(batch):
                                batch_ok = True
                            break
                        # Total shape mismatch — retry in batch mode, then fall
                        # back to per-article for the whole batch.
                        if attempt < MAX_ATTEMPTS - 1:
                            delay = min(2 ** attempt, MAX_BACKOFF_BUDGET - total_waited)
                            if delay > 0:
                                await asyncio.sleep(delay)
                                total_waited += delay
                            continue
                        print(f"[WARN] batch({len(batch)}) parse failed → per-article fallback")
                        break
                    if err:
                        print(f'[WARN] batch({len(batch)}) request failed (status {status})')
                    else:
                        print(f"[WARN] batch({len(batch)}) analyse: empty response (status {status})")
                    break
                except Exception as exc:
                    if attempt == MAX_ATTEMPTS - 1:
                        print(f'[WARN] batch({len(batch)}) request failed ({type(exc).__name__})')
                        break
                    delay = min(2 ** attempt, MAX_BACKOFF_BUDGET - total_waited)
                    if delay <= 0:
                        break
                    await asyncio.sleep(delay)
                    total_waited += delay

    if batch_ok:
        return
    # Fallback: run each article as its own single-item batch. sem is already
    # released here, so each fallback call re-acquires it independently — and
    # they can run in parallel up to ANALYSE_CONCURRENCY.
    fallback = [a for a in batch if a["id"] not in applied_ids]
    if fallback:
        await asyncio.gather(*(
            _analyse_one(session, a, sem, cache, save_lock, counter)
            for a in fallback
        ))


def _ensure_analysis_defaults(articles: list) -> None:
    for a in articles:
        if not a.get("summary"):
            # Leave empty so the renderer can show a "pending AI" placeholder
            # instead of misleading users with the title echoed as a summary.
            a["summary"] = ""
        if not isinstance(a.get("score"), int):
            a["score"] = 5
        if not isinstance(a.get("tags"), list):
            a["tags"] = []
        if a.get("sentiment") not in ("positive", "negative", "neutral"):
            a["sentiment"] = "neutral"
        if not isinstance(a.get("headline_fit"), int):
            a["headline_fit"] = None
        if not isinstance(a.get("topic"), str):
            a["topic"] = ""
        if not isinstance(a.get("event_type"), str):
            a["event_type"] = ""
        a["entities"] = _normalise_entities(a.get("entities"))
        a["key_sentences"] = _normalise_key_sentences(a.get("key_sentences"))
        a["upcoming_events"] = _validated_events(a, a.get("upcoming_events"))
        a["summary_evidence"] = validate_summary_evidence(a, a.get("summary_evidence"))
        _retain_supported_points(a)
        a["summary_evidence_status"] = "available" if a["summary_evidence"] else "unavailable"


def _fair_candidates(candidates):
    """Missing before upgrades; alternate old/new misses and rotate sources.

    Admission still applies the same per-source cap after this ordering.
    No work, retry or time budget is enlarged by a different queue position.
    """
    for has_summary in (False, True):
        groups = defaultdict(list)
        for article in candidates:
            if bool(article.get('summary')) == has_summary:
                groups[article.get('source', '')].append(article)
        queues = []
        for group in groups.values():
            if has_summary:
                queues.append(deque(group))
            else:
                old = [a for a in group if a.get('_analysis_backlog')]
                new = [a for a in group if not a.get('_analysis_backlog')]
                queues.append(deque(a for pair in zip_longest(old, new)
                                    for a in pair if a is not None))
        while any(queues):
            for queue in queues:
                if queue:
                    yield queue.popleft()


async def analyse_all(articles: list) -> list:
    if not MINIMAX_API_KEY:
        print("[analyse] Skipped — set MINIMAX_API_KEY")
        for article in articles:
            original = article.pop('_unit_marker_repair_from', None)
            if original and article.get('analysis_input_hash') == analysis_input_hash(dict(article, content=original)):
                article['analysis_input_hash'] = analysis_input_hash(article)
            if _same_cached_input(article, article):
                if article.get('summary_format') != SUMMARY_FORMAT:
                    article['summary_status'] = 'legacy'
            elif article.get("analysis_input_hash") != analysis_input_hash(article) or article.get("analysis_version") != ANALYSIS_VERSION:
                for field in ANALYSIS_FIELDS:
                    article.pop(field, None)
        _ensure_analysis_defaults(articles)
        return articles

    cache = load_cache()

    # Hydrate cached articles up front; collect the rest for batched calls.
    pending: list = []
    candidates: list = []
    admitted_by_source = {}
    cached_count = 0
    for a in articles:
        aid = a["id"]
        original = a.pop('_unit_marker_repair_from', None)
        no_text = not (a.get('content') or a.get('rss_content')) or not _article_text(a).strip()
        minimal = (a.get('content_quality') or {}).get('fallback') == 'minimal'
        if no_text or minimal:
            prior = cache.get(aid) or _published_cache_record(a) or {}
            # Existing compact text can remain readable, but a placeholder or
            # image-only body must never admit new paid analysis.
            keep_legacy = (prior.get('summary_format') != SUMMARY_FORMAT and
                           bool(prior.get('summary')) and _same_cached_input(prior, a))
            for field in ANALYSIS_FIELDS:
                a.pop(field, None)
            if keep_legacy:
                _restore_cached(a, prior)
            else:
                cache.pop(aid, None)
            a['analysis_status'] = 'pending-source'
            a['_paid_enrichment_deferred'] = True
            count('admission', 'minimal_source' if minimal else 'empty_source_text')
            continue
        if original:
            previous = dict(a, content=original)
            cached = cache.get(aid) or {}
            if not _needs_full_analysis(cached, a):
                pass  # Already migrated in a prior ordinary cycle.
            elif not _needs_full_analysis(cached, previous):
                cached['input_hash'] = analysis_input_hash(a)
                cached['summary_evidence'] = validate_summary_evidence(dict(a, summary=cached.get('summary')), cached.get('summary_evidence'))
                cached['key_sentences'] = [s for s in cached.get('key_sentences', []) if verified_quote(s, a)]
            else:
                # No paid refresh solely to remove input-unit labels.
                for field in ('summary', 'summary_evidence', 'key_sentences', 'analysis_input_hash', 'analysis_version'):
                    a.pop(field, None)
                _ensure_analysis_defaults([a])
                continue
        # An interrupted prior cycle may have published a successful result
        # before its final cache flush. Reuse its exact input-bound record
        # rather than paying to generate the same result again.
        if _needs_full_analysis(cache.get(aid), a):
            published = _published_cache_record(a)
            if published is not None:
                cache[aid] = published
        if aid in cache and not _needs_full_analysis(cache[aid], a):
            c = cache[aid]
            cached_count += 1
            a['analysis_status'] = 'available'
            a.pop('_paid_enrichment_deferred', None)
            a["analysis_input_hash"] = c["input_hash"]
            a["analysis_version"] = ANALYSIS_VERSION
            a["summary"]   = c.get("summary", "")
            a["summary_evidence"] = validate_summary_evidence(a, c.get("summary_evidence"))
            for field in SUMMARY_FIELDS:
                if field in c:
                    a[field] = c[field]
                else:
                    a.pop(field, None)
            a["score"]     = c["score"] if c.get("score") is not None else 5
            a["tags"]      = c.get("tags", [])
            a["sentiment"] = c.get("sentiment", "neutral")
            a["headline_fit"] = c.get("headline_fit")
            a["topic"]     = c.get("topic", "")
            a["event_type"] = c.get("event_type", "")
            a["entities"]   = _normalise_entities(c.get("entities"))
            c['key_sentences'] = _normalise_key_sentences(c.get('key_sentences'), a)
            a["key_sentences"] = c['key_sentences']
            a['key_sentences_content_hash'] = hashlib.sha256((a.get('content') or '').encode('utf-8')).hexdigest()
            a["upcoming_events"] = _validated_events(a, c.get("upcoming_events"))
        else:
            legacy = cache.get(aid) or {}
            keep_legacy = _same_cached_input(legacy, a)
            if not keep_legacy:
                cache.pop(aid, None)
            # A failed refresh must not restore analysis of a corrected input.
            for field in ANALYSIS_FIELDS:
                a.pop(field, None)
            if keep_legacy:
                _restore_cached(a, legacy)
            candidates.append(a)

    # A safe compact summary remains useful while its format is upgraded.
    # Admit genuinely missing summaries first, inside the same source caps.
    for a in _fair_candidates(candidates):
        source = a.get('source', '')
        admitted = admitted_by_source.get(source, 0)
        if admitted >= legacy_source_limit(source):
            a['analysis_status'] = 'pending-budget'
            a['_paid_enrichment_deferred'] = True
            count('admission', 'source_budget_deferred')
            continue
        admitted_by_source[source] = admitted + 1
        a['analysis_status'] = 'pending'
        a.pop('_paid_enrichment_deferred', None)
        pending.append(a)

    print(f"[analyse] {cached_count} cached, {len(pending)} to generate (batch={BATCH_SIZE})")
    upgrades = sum(bool(a.get('summary')) for a in pending)
    count('admission', 'cached', cached_count)
    count('admission', 'missing', len(pending) - upgrades)
    count('admission', 'upgrades', upgrades)
    stats = CURRENT.get()
    if stats is not None:
        stats.admitted_ids.update(a['id'] for a in pending)
    print(f"[analyse] admitted {len(pending) - upgrades} missing summaries, {upgrades} format upgrades")

    batches = [pending[i:i + BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]

    sem       = asyncio.Semaphore(ANALYSE_CONCURRENCY)
    save_lock = asyncio.Lock()
    counter   = [0]
    active_ids = {a["id"] for a in articles}
    dropped = 0
    try:
        async with aiohttp.ClientSession() as session:
            queue = deque(batches)
            async def worker():
                while queue:
                    batch = queue.popleft()
                    try:
                        await _analyse_batch(session, batch, sem, cache, save_lock, counter)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        count('results', 'batch_crashed')
                        print(f'[WARN] analyse worker batch failed ({type(exc).__name__})')
            # Only active workers may create fallback tasks. They finish that
            # recovery before taking another batch, so fallback never waits
            # behind every remaining source batch.
            tasks = [worker() for _ in range(min(ANALYSE_CONCURRENCY, len(batches)))]
            # One failed batch must not discard successful siblings.
            results = await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        # The build deliberately cancels this stage at its deadline. Preserve
        # every completed result even below SAVE_CACHE_EVERY, then propagate
        # cancellation so the build still records an incomplete stage.
        pruned = {k: v for k, v in cache.items() if k in active_ids}
        dropped = len(cache) - len(pruned)
        save_cache(pruned)
    for r in results:
        if isinstance(r, BaseException):
            print(f'[WARN] analyse worker failed ({type(r).__name__})')

    done = sum(1 for a in articles if a.get("summary"))
    print(f"[analyse] {done}/{len(articles)} articles analysed"
          + (f" (pruned {dropped} stale cache entries)" if dropped else ""))

    # Safety net: articles that failed analysis (API timeout / no content) get
    # minimal defaults so downstream steps and tests never see missing fields.
    _ensure_analysis_defaults(articles)
    for article in pending:
        if article.get('analysis_status') == 'pending':
            article['analysis_status'] = 'failed'

    return articles
