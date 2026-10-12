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


def test_explicit_start_dates_and_consistent_parenthetical_days():
    for expression in ("10月13日起", "10月13日 10:00起", "明日(13日)", "明日（13）"):
        assert resolve_expression(expression, date(2026, 10, 12)) == date(2026, 10, 13)
    for expression in ("明日(14日)", "10月13日25:00起", "10月13日起至15日", "約10月13日起"):
        assert resolve_expression(expression, date(2026, 10, 12)) is None


def test_start_dates_remain_source_grounded_and_qualified_dates_uncertain():
    for expression, source, expected in (
        ("10月13日起", "展覽10月13日起舉行", "2026-10-13"),
        ("10月14日 10:00起", "展覽10月14日 10:00起舉行", "2026-10-14"),
        ("明日(13日)", "展覽明日(13日)舉行", "2026-10-13"),
        ("10月13日起", "展覽約10月13日起舉行", None),
        ("明日", "展覽明日(14日)舉行", None),
        ("10月13日", "展覽10月13日起至15日舉行", None),
        ("10月13日", "展覽10月13日10:00起至15日舉行", None),
    ):
        event = validate_event(dict(title="展覽", date_expression=expression, evidence=source),
                               title="消息", text=source, published="2026-10-12T00:00:00Z")
        assert event["date"] == expected


def test_exact_iphone_on_sale_alias_does_not_merge_other_occurrences():
    assert event_identity("iPhone Duo 正式開售") == event_identity("iPhone Duo 開售")
    for other in ("iPhone Duo 預購", "iPhone Duo 宣布開售", "iPhone Duo 發布", "iPhone Air 開售"):
        assert event_identity(other) != event_identity("iPhone Duo 開售")
    assert event_identity("Apple 新產品發布") != event_identity("Apple Welcome Home 活動")


def test_immediate_qualified_date_with_adverbs_and_narrowed_quote():
    for source in ("新產品預計均於10月13日亮相", "新產品可能都將於10月13日亮相",
                   "新產品10月13日（暫定）亮相", "新產品10月13日左右亮相"):
        event = validate_event(dict(title="產品亮相", date_expression="10月13日", evidence="10月13日"),
                               title="消息", text=source, published="2026-10-12")
        assert event["date"] is None
    # Qualifiers separated by another fact or sentence must not leak across.
    event = validate_event(dict(title="產品亮相", date_expression="10月13日", evidence="10月13日"),
                           title="消息", text="新手機預計11月上市。平板於10月13日亮相", published="2026-10-12")
    assert event["date"] == "2026-10-13"
