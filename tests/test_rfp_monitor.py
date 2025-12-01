import datetime as dt
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import rfp_monitor
from rfp_monitor import normalize_text, diff_new_items, format_report


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
