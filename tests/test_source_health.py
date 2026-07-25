import json
from datetime import datetime, timedelta, timezone

import pytest

from src import source_health as SH


NOW = datetime(2026, 7, 25, 12, 0, tzinfo=timezone.utc)


def _stats(**counts):
    return {name: {"effective_count": n} for name, n in counts.items()}


def test_healthy_source_is_not_tracked():
    state, events = SH.evaluate_sources(_stats(**{"RTHK 本地": 20}), now=NOW)
    assert state["sources"] == {}
    assert events == []


def test_first_zero_cycle_starts_clock_without_alerting():
    state, events = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=NOW)
    assert events == []
    assert state["sources"]["SkyPost"]["zero_since"] == NOW.isoformat()


def test_no_alert_before_threshold():
    state, _ = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=NOW)
    later = NOW + timedelta(hours=SH.ZERO_ALERT_AFTER_HOURS - 1)
    state, events = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=later, state=state)
    assert events == []


def test_alerts_once_threshold_is_crossed():
    state, _ = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=NOW)
    later = NOW + timedelta(hours=SH.ZERO_ALERT_AFTER_HOURS)
    state, events = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=later, state=state)
    assert [e["kind"] for e in events] == ["dead"]
    assert events[0]["source"] == "SkyPost"


def test_does_not_renag_every_build_while_dead():
    state, _ = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=NOW)
    t1 = NOW + timedelta(hours=SH.ZERO_ALERT_AFTER_HOURS)
    state, events = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=t1, state=state)
    assert len(events) == 1
    # 20 minutes later — the very next build — must stay quiet.
    t2 = t1 + timedelta(minutes=20)
    state, events = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=t2, state=state)
    assert events == []


def test_reminds_again_after_remind_window():
    state, _ = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=NOW)
    t1 = NOW + timedelta(hours=SH.ZERO_ALERT_AFTER_HOURS)
    state, _ = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=t1, state=state)
    t2 = t1 + timedelta(hours=SH.REMIND_EVERY_HOURS)
    state, events = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=t2, state=state)
    assert [e["kind"] for e in events] == ["dead"]


def test_recovery_emits_event_and_clears_state():
    state, _ = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=NOW)
    t1 = NOW + timedelta(hours=SH.ZERO_ALERT_AFTER_HOURS)
    state, _ = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=t1, state=state)
    state, events = SH.evaluate_sources(_stats(**{"SkyPost": 5}), now=t1, state=state)
    assert [e["kind"] for e in events] == ["recovered"]
    assert state["sources"] == {}


def test_silent_recovery_when_never_alerted():
    # Zero for one cycle then back — nobody needs to hear about it.
    state, _ = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=NOW)
    state, events = SH.evaluate_sources(_stats(**{"SkyPost": 3}), now=NOW, state=state)
    assert events == []
    assert state["sources"] == {}


def test_falls_back_to_count_when_effective_count_missing():
    stats = {"法庭線": {"count": 4}}
    state, events = SH.evaluate_sources(stats, now=NOW)
    assert state["sources"] == {}


def test_zero_clock_survives_across_many_builds():
    # The real failure mode: SkyPost sat at 0 for a month. zero_since must
    # keep pointing at the FIRST zero, not get reset each build.
    state, _ = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=NOW)
    t = NOW
    for _ in range(10):
        t += timedelta(minutes=20)
        state, _ = SH.evaluate_sources(_stats(**{"SkyPost": 0}), now=t, state=state)
    assert state["sources"]["SkyPost"]["zero_since"] == NOW.isoformat()


def test_format_dead_and_recovered():
    assert "來源斷更" in SH._format({"source": "SkyPost", "kind": "dead", "hours": 48})
    assert "2.0 日" in SH._format({"source": "SkyPost", "kind": "dead", "hours": 48})
    assert "來源恢復" in SH._format({"source": "SkyPost", "kind": "recovered", "hours": 0})


def test_format_escapes_html():
    out = SH._format({"source": "<b>x</b>", "kind": "recovered", "hours": 0})
    assert "&lt;b&gt;" in out


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(SH, "STATE_PATH", tmp_path / "source_health.json")
    SH._save_state({"sources": {"A": {"zero_since": NOW.isoformat()}}})
    assert SH._load_state()["sources"]["A"]["zero_since"] == NOW.isoformat()


def test_load_state_tolerates_corrupt_file(tmp_path, monkeypatch):
    p = tmp_path / "source_health.json"
    p.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(SH, "STATE_PATH", p)
    assert SH._load_state() == {"sources": {}}


def test_check_source_health_persists_and_sends(tmp_path, monkeypatch):
    import asyncio
    monkeypatch.setattr(SH, "STATE_PATH", tmp_path / "source_health.json")
    monkeypatch.setattr(SH, "TELEGRAM_BOT_TOKEN", "")   # no real Telegram POST
    asyncio.run(SH.check_source_health(_stats(**{"SkyPost": 0}), now=NOW))
    saved = json.loads((tmp_path / "source_health.json").read_text(encoding="utf-8"))
    assert saved["sources"]["SkyPost"]["zero_since"] == NOW.isoformat()
