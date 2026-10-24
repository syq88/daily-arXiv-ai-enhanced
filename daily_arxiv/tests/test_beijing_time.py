import contextlib
from datetime import date, datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "daily_arxiv" / "check_stats.py"
spec = importlib.util.spec_from_file_location("check_stats", MODULE_PATH)
check_stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_stats)


class BeijingRunDateTests(unittest.TestCase):
    def assert_beijing_date(self, instant, expected):
        class FrozenDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                if tz is None:
                    raise AssertionError("Current date must use an explicit timezone")
                return instant.astimezone(tz)

        with (
            patch.dict(os.environ, {"TZ": "UTC", "ARXIV_RUN_DATE": ""}),
            patch.object(check_stats, "datetime", FrozenDateTime),
        ):
            self.assertEqual(expected, check_stats.get_run_date())

    def test_beijing_date_after_utc_1600(self):
        self.assert_beijing_date(
            datetime(2026, 10, 23, 20, 33, tzinfo=timezone.utc),
            date(2026, 10, 24),
        )

    def test_beijing_date_before_utc_1600(self):
        self.assert_beijing_date(
            datetime(2026, 10, 23, 15, 59, tzinfo=timezone.utc),
            date(2026, 10, 23),
        )

    def test_beijing_date_rolls_over_at_new_year(self):
        self.assert_beijing_date(
            datetime(2026, 12, 31, 16, 0, tzinfo=timezone.utc),
            date(2027, 1, 1),
        )

    def test_captured_run_date_survives_midnight(self):
        with (
            patch.dict(os.environ, {"ARXIV_RUN_DATE": "2026-10-24"}),
            patch.object(check_stats, "datetime") as clock,
        ):
            self.assertEqual(date(2026, 10, 24), check_stats.get_run_date())
            clock.now.assert_not_called()

    def test_deduplication_uses_the_captured_date_for_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "daily_arxiv").mkdir()
            (root / "data").mkdir()
            today = root / "data/2026-10-24.jsonl"
            history = root / "data/2026-10-23.jsonl"
            today.write_text(
                '{"id":"old"}\n{"id":"new"}\n', encoding="utf-8"
            )
            history.write_text('{"id":"old"}\n', encoding="utf-8")
            previous = Path.cwd()
            try:
                os.chdir(root / "daily_arxiv")
                with (
                    patch.dict(os.environ, {"ARXIV_RUN_DATE": "2026-10-24"}),
                    patch.object(check_stats, "datetime") as clock,
                    contextlib.redirect_stderr(io.StringIO()),
                ):
                    self.assertEqual("has_new_content", check_stats.perform_deduplication())
                    clock.now.assert_not_called()
            finally:
                os.chdir(previous)
            papers = [json.loads(line) for line in today.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([{"id": "new"}], papers)


if __name__ == "__main__":
    unittest.main()
