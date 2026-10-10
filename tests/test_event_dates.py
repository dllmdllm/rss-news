from datetime import date

from src.event_dates import event_identity, publication_day, resolve_expression, validate_event


def test_relative_monday_uses_publication_not_build_day():
    for published in ("2026-10-09T13:39:00+00:00", "2026-10-09T21:39:00+08:00"):
        event = validate_event({"date": "2026-10-13", "title": "新皇崗口岸正式通關",
                                "date_expression": "下周一", "evidence": "皇崗口岸將於下周一開通"},
                               title="新聞", text="皇崗口岸將於下周一開通", published=published)
        assert event["date"] == "2026-10-12"
        assert event["precision"] == "day"


def test_uncertain_month_end_never_becomes_exact_day():
    event = validate_event({"date": "2026-10-31", "title": "公布報告",
                            "date_expression": "月底", "evidence": "報告月底公布"},
                           title="報告月底公布", text="", published="2026-10-09T00:00:00Z")
    assert event["date"] is None
    assert event["date_expression"] == "月底"
    assert event["precision"] == "uncertain"


def test_event_requires_verbatim_source_and_date_expression():
    for event in ({"date": "2026-10-31", "title": "報告"},
                  {"title": "報告", "date_expression": "月底", "evidence": "捏造月底公布"},
                  {"title": "報告", "date_expression": "10月31日", "evidence": "報告月底公布"}):
        assert validate_event(event, title="報告月底公布", text="", published="2026-10-09") is None


def test_invalid_and_ambiguous_dates_stay_uncertain():
    for expression in ("2026-02-30", "10月12日至15日", "年初", "early 2027", "下周", "Monday"):
        assert resolve_expression(expression, date(2026, 10, 9)) is None
    assert resolve_expression("下周一", None) is None
    assert resolve_expression("2026-10-12", None) == date(2026, 10, 12)


def test_publication_date_and_relative_days_are_hkt():
    day = publication_day("2026-10-09T17:30:00Z")
    assert day == date(2026, 10, 10)
    assert resolve_expression("明日", day) == date(2026, 10, 11)
    assert resolve_expression("下星期一", day) == date(2026, 10, 12)


def test_narrow_opening_identity_never_merges_bus_service():
    assert event_identity("新皇崗口岸正式開通") == event_identity("皇崗新口岸早上6時半通關")
    assert event_identity("九巴皇崗口岸專線開通") != event_identity("皇崗口岸開通")


def test_narrowed_range_and_qualified_dates_stay_uncertain():
    for text, evidence in [('展覽10月12日至15日舉行', '展覽10月12日至15日舉行'),
                           ('展覽約10月12日舉行', '10月12日'),
                           ('展覽10月12日前後舉行', '10月12日'),
                           ('展覽暫定於10月12日舉行', '展覽暫定於10月12日舉行')]:
        result = validate_event(dict(title='展覽', date_expression='10月12日', evidence=evidence),
                                title='展覽消息', text=text, published='2026-10-09')
        assert result['date'] is None
        assert result['precision'] == 'uncertain'


def test_other_approximate_date_does_not_qualify_exact_event_date():
    result = validate_event(dict(title='展覽', date_expression='10月12日', evidence='展覽10月12日舉行'),
                            title='展覽消息', text='門票約10月1日發售。展覽10月12日舉行。',
                            published='2026-10-09')
    assert result['date'] == '2026-10-12'


def test_narrowed_alternative_negated_and_year_qualified_dates_stay_uncertain():
    for text, expression in [('會議10月12日或13日舉行', '10月12日'),
                             ('會議不一定於10月12日舉行', '10月12日'),
                             ('慶典明年1月1日舉行', '1月1日')]:
        result = validate_event(dict(title='活動', date_expression=expression, evidence=expression),
                                title='消息', text=text, published='2026-10-09')
        assert result['date'] is None
        assert result['precision'] == 'uncertain'
