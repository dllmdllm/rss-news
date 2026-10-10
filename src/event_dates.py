"""Conservative, source-grounded event dates. All relative dates use HKT.

Unrecognised or approximate expressions stay uncertain; a model's proposed
calendar date is never sufficient evidence for a day in the calendar.
"""
import re
from datetime import date, datetime, timedelta, timezone

HKT = timezone(timedelta(hours=8))
EVENT_DATE_VERSION = "source-date-v3"


def publication_day(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Historic naive feed dates follow the backend's UTC convention.
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).astimezone(HKT).date()
    except (ValueError, TypeError):
        return None


def resolve_expression(expression, published):
    expression = re.sub(r"\s+", "", str(expression or ""))
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", expression):
            return date.fromisoformat(expression)
        match = re.fullmatch(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})(?:日|號)", expression)
        if match and (match[1] or published):
            return date(int(match[1] or published.year), int(match[2]), int(match[3]))
        match = re.fullmatch(r"(下|本|今)(?:周|週|星期)([一二三四五六日天])", expression)
        if match and published:
            weekday = "一二三四五六日".index(match[2].replace("天", "日"))
            return published - timedelta(days=published.weekday()) + timedelta(days=weekday + (7 if match[1] == "下" else 0))
        if published and expression in ("明日", "明天", "後日", "後天", "今日", "今天"):
            return published + timedelta(days={"明日": 1, "明天": 1, "後日": 2, "後天": 2, "今日": 0, "今天": 0}[expression])
    except (ValueError, OverflowError):
        pass
    return None


def _expression_is_uncertain(expression, evidence, source):
    """Do not let a model narrow a qualified date or one end of a range.

    Inspect only the date's immediate surroundings at the quoted source location,
    including qualifiers the quote itself may have omitted.
    """
    expression, evidence, source = (re.sub(r"\s+", "", value)
                                    for value in (expression, evidence, source))
    for quote_match in re.finditer(re.escape(evidence), source):
        for date_match in re.finditer(re.escape(expression), evidence):
            start = quote_match.start() + date_match.start()
            end = quote_match.start() + date_match.end()
            before, after = source[max(0, start - 20):start], source[end:end + 20]
            if re.search(r"(?:大約|約|暫定|預計|可能|最早|最遲|不早於|不遲於|不一定|未必|不會|並非|不是)(?:在|於|為)?$", before):
                return True
            # A model may omit a year qualifier from its quoted expression.
            # Keep that narrowed date uncertain instead of assuming this year.
            if re.search(r"(?:明年|翌年|去年|前年)(?:在|於)?$", before):
                return True
            if re.search(r"(?:\d|日|號|[一二三四五六天])(?:至|到|[–—~～-])$", before):
                return True
            if re.match(r"(?:至|到|[–—~～-])(?:\d|下|本|今|明|後)", after):
                return True
            if re.match(r"(?:或|或者|及|或於)(?:\d|下|本|今|明|後)", after):
                return True
            if re.match(r"(?:左右|前後|之前|之後|以前|以後)", after):
                return True
    return False


def validate_event(event, *, title, text, published):
    """Validate a verbatim quote and derive its date independently of AI."""
    if not isinstance(event, dict):
        return None
    evidence = re.sub(r"\s+", " ", str(event.get("evidence") or "")).strip()
    expression = str(event.get("date_expression") or "").strip()
    event_title = str(event.get("title") or "").strip()[:30]
    source = re.sub(r"\s+", " ", f"{title} {text}")
    if not (event_title and expression and evidence and evidence in source):
        return None
    if re.sub(r"\s+", "", expression) not in re.sub(r"\s+", "", evidence):
        return None
    resolved = (None if _expression_is_uncertain(expression, evidence, source)
                else resolve_expression(expression, publication_day(published)))
    return {"date": resolved.isoformat() if resolved else None,
            "title": event_title, "date_expression": expression[:80],
            "evidence": evidence[:500], "precision": "day" if resolved else "uncertain",
            "date_version": EVENT_DATE_VERSION}


def event_identity(title):
    """Only explicitly equivalent titles merge; no broad/fuzzy entity merge."""
    compact = re.sub(r"[\s，。︱｜:：、]", "", str(title)).lower()
    if ("皇崗" in compact and "口岸" in compact
            and any(term in compact for term in ("開通", "啟用", "通關"))
            and not any(term in compact for term in ("巴士", "專線", "路線", "e道", "登記", "關閉"))):
        return "皇崗口岸啟用"
    return compact
