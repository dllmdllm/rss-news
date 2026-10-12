from datetime import datetime, timedelta

from src.daily_brief import (
    HKT,
    _parse_brief,
    _telegram_text,
    select_top_articles,
    should_generate,
)


def test_should_generate_once_per_day_after_6am():
    morning = datetime(2026, 7, 13, 7, 0, tzinfo=HKT)
    assert should_generate({}, morning)
    assert should_generate({"date": "2026-07-12"}, morning)
    assert not should_generate({"date": "2026-07-13"}, morning)
    # 06:00 之前唔生成
    early = datetime(2026, 7, 13, 5, 59, tzinfo=HKT)
    assert not should_generate({}, early)


def test_select_top_articles_dedupes_clusters_and_respects_cutoff():
    now = datetime(2026, 7, 13, 7, 0, tzinfo=HKT)
    fresh = (now - timedelta(hours=2)).isoformat()
    stale = (now - timedelta(hours=30)).isoformat()
    articles = [
        {"id": "a", "date": fresh, "score": 9, "cluster_id": "c1"},
        {"id": "b", "date": fresh, "score": 8, "cluster_id": "c1"},   # same cluster → skipped
        {"id": "c", "date": fresh, "score": 7, "cluster_id": "c2"},
        {"id": "d", "date": stale, "score": 10, "cluster_id": "c3"},  # too old → skipped
        {"id": "e", "date": fresh, "score": 6, "cluster_id": None,
         "duplicate_of": "a"},                                        # duplicate → skipped
    ]
    picked = [a["id"] for a in select_top_articles(articles, now)]
    assert picked == ["a", "c"]


def test_select_top_articles_handles_utc_offset_dates_correctly():
    # fetch.py 寫嘅真實 article date 係 UTC（+00:00），唔係 HKT——呢個 test
    # 用真實嘅 offset 組合，regression 住 2026-07-21 個 string-比較 timezone bug
    # （之前令 24h 窗口日日縮水到 16-22h）。
    now = datetime(2026, 7, 21, 6, 5, tzinfo=HKT)  # 早報固定生成時間點
    from datetime import timezone as _tz
    fresh_utc = (now.astimezone(_tz.utc) - timedelta(hours=23, minutes=30)).isoformat()
    stale_utc = (now.astimezone(_tz.utc) - timedelta(hours=25)).isoformat()
    articles = [
        {"id": "fresh", "date": fresh_utc, "score": 9, "cluster_id": "c1"},
        {"id": "stale", "date": stale_utc, "score": 10, "cluster_id": "c2"},
    ]
    picked = [a["id"] for a in select_top_articles(articles, now)]
    assert picked == ["fresh"]


def test_parse_brief_strips_fences_and_validates_ids():
    raw = """```json
    {"title": "今日焦點", "text": "早報正文內容。",
     "highlights": [
       {"point": "重點一", "id": "a"},
       {"point": "重點二", "id": "unknown"}
     ]}
    ```"""
    brief = _parse_brief(raw, valid_ids={"a"})
    assert brief["title"] == "今日焦點"
    assert brief["highlights"][0] == {"point": "重點一", "id": "a"}
    # 唔認識嘅 id 會被清空，唔會產生死 link
    assert brief["highlights"][1] == {"point": "重點二", "id": ""}


def test_parse_brief_rejects_empty_body():
    assert _parse_brief('{"title": "x", "text": "", "highlights": []}', set()) is None
    assert _parse_brief("not json at all", set()) is None


def test_telegram_text_omits_paragraph_to_avoid_duplication():
    # 網站早報卡先顯示 brief["text"]（150-250 字段落）；Telegram 淨係標題 +
    # bullets——兩樣一齊出會令手機屏幕出現重複內容（2026-07-13 用戶反映）。
    brief = {
        "title": "今日焦點",
        "text": "呢段長 paragraph 唔應該出現喺 Telegram 度。",
        "highlights": [{"point": "重點一", "id": "a"}, {"point": "重點二", "id": ""}],
    }
    text = _telegram_text(brief, "7月13日")
    assert "今日早報 · 7月13日" in text
    assert "今日焦點" in text
    assert "・重點一" in text
    assert "・重點二" in text
    assert "呢段長 paragraph" not in text


def test_saved_brief_retries_delivery_without_regeneration(monkeypatch, tmp_path):
    import asyncio
    import json
    from src import daily_brief as DB
    fixed = datetime(2026, 10, 12, 7, tzinfo=HKT)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    monkeypatch.setattr(DB, 'datetime', Clock)
    monkeypatch.setattr(DB, 'OUTPUT_PATH', tmp_path / 'brief.json')
    monkeypatch.setattr(DB, 'MINIMAX_API_KEY', '')
    monkeypatch.setattr(DB, 'TELEGRAM_BOT_TOKEN', 'fake-token')
    DB._save({'date': '2026-10-12', 'title': '焦點', 'text': '保存正文。',
              'telegram_delivery_pending': True,
              'highlights': [{'point': '重點', 'id': 'a'}]})
    async def forbidden(*args, **kwargs):
        raise AssertionError('saved text must not be regenerated')
    monkeypatch.setattr(DB, 'post_messages', forbidden)
    statuses = iter([503, 200])
    calls = []
    async def send(session, text, **kwargs):
        calls.append(text)
        return next(statuses)
    monkeypatch.setattr(DB, '_send_telegram', send)
    asyncio.run(DB.generate_daily_brief([]))
    assert not DB._load_existing().get('telegram_delivered_at')
    asyncio.run(DB.generate_daily_brief([]))
    assert DB._load_existing()['telegram_delivered_at'] == fixed.isoformat()
    asyncio.run(DB.generate_daily_brief([]))
    assert len(calls) == 2 and calls[0] == calls[1]
    assert json.loads(DB.OUTPUT_PATH.read_text(encoding="utf-8"))['text'] == '保存正文。'


def test_delivery_exception_keeps_saved_brief_pending(monkeypatch, tmp_path):
    import asyncio
    from src import daily_brief as DB
    monkeypatch.setattr(DB, 'OUTPUT_PATH', tmp_path / 'brief.json')
    monkeypatch.setattr(DB, 'TELEGRAM_BOT_TOKEN', 'fake-token')
    payload = {'date': '2026-10-12', 'text': '正文', 'highlights': [{'point': '重點'}],
               'telegram_delivery_pending': True}
    DB._save(payload)
    async def fail(*args, **kwargs):
        raise TimeoutError('simulated')
    monkeypatch.setattr(DB, '_send_telegram', fail)
    asyncio.run(DB._deliver_brief(None, payload, datetime(2026, 10, 12, 7, tzinfo=HKT)))
    assert not DB._load_existing().get('telegram_delivered_at')


def test_legacy_brief_unknown_delivery_does_not_resend(monkeypatch, tmp_path):
    import asyncio
    from src import daily_brief as DB
    monkeypatch.setattr(DB, 'OUTPUT_PATH', tmp_path / 'brief.json')
    monkeypatch.setattr(DB, 'TELEGRAM_BOT_TOKEN', 'fake-token')
    legacy = {'date': '2026-10-12', 'text': '正文', 'highlights': [{'point': '重點'}]}
    DB._save(legacy)
    async def forbidden(*args, **kwargs):
        raise AssertionError('unknown legacy delivery must not trigger a duplicate')
    monkeypatch.setattr(DB, '_send_telegram', forbidden)
    asyncio.run(DB._deliver_brief(None, legacy, datetime(2026, 10, 12, 7, tzinfo=HKT)))
    assert DB._load_existing() == legacy


def test_new_brief_is_durable_before_failed_delivery_and_not_regenerated(monkeypatch, tmp_path):
    import asyncio
    import json
    from src import daily_brief as DB
    fixed = datetime(2026, 10, 12, 7, tzinfo=HKT)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    monkeypatch.setattr(DB, 'datetime', Clock)
    monkeypatch.setattr(DB, 'OUTPUT_PATH', tmp_path / 'brief.json')
    monkeypatch.setattr(DB, 'MINIMAX_API_KEY', 'fake-key')
    monkeypatch.setattr(DB, 'TELEGRAM_BOT_TOKEN', 'fake-token')
    generations = []
    async def generate(*args, **kwargs):
        generations.append(1)
        return json.dumps({'title': '焦點', 'text': '正文',
                           'highlights': [{'point': '重點', 'id': 'a0'}]}), {}, 200
    monkeypatch.setattr(DB, 'post_messages', generate)
    async def fail(*args, **kwargs):
        assert DB._load_existing()['text'] == '正文'
        return 500
    monkeypatch.setattr(DB, '_send_telegram', fail)
    articles = [{'id': f'a{i}', 'date': fixed.isoformat(), 'score': 8} for i in range(5)]
    asyncio.run(DB.generate_daily_brief(articles))
    asyncio.run(DB.generate_daily_brief(articles))
    assert len(generations) == 1
    assert not DB._load_existing().get('telegram_delivered_at')
