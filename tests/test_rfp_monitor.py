import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rfp_monitor
from rfp_monitor import normalize_text, diff_new_items, format_report, trade_drop, llm_filter, llm_model


def test_normalize_text_strips_and_lowercases():
    assert normalize_text("  Hello   WORLD  ") == "hello world"


def test_diff_new_items_updates_seen(tmp_path):
    source = type("S", (), {"name": "Example"})  # simple stub with name attribute
    seen = {"Example": ["https://old.com"]}
    items = [
        {"title": "old", "url": "https://old.com"},
        {"title": "new", "url": "https://new.com"},
    ]
    new = diff_new_items(source, items, seen)
    assert new == [{"title": "new", "url": "https://new.com"}]
    assert "https://new.com" in seen["Example"]


def test_format_report_handles_results(monkeypatch):
    fixed_dt = dt.datetime(2024, 1, 1, 12, 30)
    monkeypatch.setattr(rfp_monitor, "datetime", type("dt", (), {"now": lambda: fixed_dt, "strftime": dt.datetime.strftime}))
    report = format_report({"Example": [{"title": "Item", "url": "https://x.com"}]})
    assert "Example" in report
    assert "Item" in report
    assert "https://x.com" in report
    assert "2024-01-01 12:30" in report


def test_trade_drop_detects_construction():
    assert trade_drop("Road Repair Service") is True
    assert trade_drop("Web Portal Development") is False


def test_llm_filter_is_noop_when_disabled():
    items = [{"title": "Portal build", "url": "https://x.com"}]
    out = llm_filter(items, {"enabled": False})
    assert out == items


def test_llm_filter_can_be_disabled_via_env(monkeypatch):
    items = [{"title": "Portal build", "url": "https://x.com"}]
    cfg = {"enabled": True, "enabled_env": "LLM_ENABLED", "api_key_env": "OPENAI_API_KEY"}
    monkeypatch.setenv("LLM_ENABLED", "0")
    monkeypatch.setenv("OPENAI_API_KEY", "dummy")
    out = llm_filter(items, cfg)
    assert out == items


def test_llm_model_prefers_env(monkeypatch):
    cfg = {"model": "gpt-5-nano", "model_env": "LLM_MODEL"}
    monkeypatch.setenv("LLM_MODEL", "gpt-xyz")
    assert llm_model(cfg) == "gpt-xyz"
