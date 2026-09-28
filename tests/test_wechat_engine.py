import tempfile
import threading
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

from wechat_engine import Plan, SubmitLatch, TZ, WindowError, date_matches, run


class Stop:
    def __init__(self):
        self.flag = False
    def is_set(self):
        return self.flag
    def wait(self, seconds):
        return self.flag


class FakePage:
    def __init__(self, stop, latch, available=True, changed=False, fail=False, missing=False):
        self.stop, self.latch = stop, latch
        self.available, self.changed, self.fail, self.missing = available, changed, fail, missing
        self.calls, self.signatures = [], 0
    def snapshot(self):
        pass
    def ready_signature(self):
        self.signatures += 1
        return "changed" if self.changed and self.signatures > 1 else "original"
    def foreground(self):
        pass
    def refresh(self):
        self.calls.append("refresh")
        return True
    def select_day(self, target):
        self.calls.append("date")
        if self.missing:
            self.stop.flag = True
        return not self.missing
    def choose_slot(self, period):
        self.calls.append("slot")
        if not self.available:
            self.stop.flag = True
        return self.available
    def submit(self, plan, expected, latch):
        self.calls.append("submit")
        latch.acquire(plan)
        if self.fail:
            raise TimeoutError()


class WindowModeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.latch = SubmitLatch(Path(self.temp.name) / "pending.json")
        self.now = datetime(2026, 9, 28, 10, 0, tzinfo=TZ)
        self.plan = Plan(self.now, date(2026, 10, 3), "上午")
        self.stop = Stop()
    def tearDown(self):
        self.temp.cleanup()
    def execute(self, page):
        run(page, self.plan, self.latch, self.stop, lambda _: None, clock=lambda: self.now)
    def test_date_exact_and_weekday(self):
        self.assertTrue(date_matches("10-3 星期六", self.plan.target))
        for text in ("10-13 星期六", "10-3 星期日", "1-3 星期六", "已选10-3 星期六"):
            self.assertFalse(date_matches(text, self.plan.target))
    def test_wrong_week_rejected(self):
        with self.assertRaises(WindowError):
            Plan(self.now, date(2026, 10, 5), "上午").validate(self.now)
    def test_too_fast_rejected(self):
        with self.assertRaises(WindowError):
            Plan(self.now, self.plan.target, "上午", 0.1).validate(self.now)
    def test_only_one_submit_then_return(self):
        page = FakePage(self.stop, self.latch)
        self.execute(page)
        self.assertEqual(page.calls, ["refresh", "date", "slot", "submit"])
        self.assertTrue(self.latch.exists())
    def test_full_slot_no_submit(self):
        page = FakePage(self.stop, self.latch, available=False)
        self.execute(page)
        self.assertNotIn("submit", page.calls)
    def test_missing_target_no_submit(self):
        page = FakePage(self.stop, self.latch, missing=True)
        self.execute(page)
        self.assertNotIn("slot", page.calls)
        self.assertNotIn("submit", page.calls)
    def test_changed_personal_data_stops_before_refresh(self):
        page = FakePage(self.stop, self.latch, changed=True)
        with self.assertRaises(WindowError):
            self.execute(page)
        self.assertEqual(page.calls, [])
    def test_uncertain_submit_latches_no_retry(self):
        page = FakePage(self.stop, self.latch, fail=True)
        with self.assertRaises(TimeoutError):
            self.execute(page)
        self.assertEqual(page.calls.count("submit"), 1)
        self.assertTrue(self.latch.exists())
        with self.assertRaises(WindowError):
            self.execute(FakePage(self.stop, self.latch))
    def test_exclusive_latch(self):
        self.latch.acquire(self.plan)
        with self.assertRaises(WindowError):
            self.latch.acquire(self.plan)
        self.assertNotIn("original", self.latch.path.read_text())
    def test_stop_before_refresh(self):
        self.stop.flag = True
        page = FakePage(self.stop, self.latch)
        self.execute(page)
        self.assertEqual(page.calls, [])


if __name__ == "__main__":
    unittest.main()
