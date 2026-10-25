import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


MODULE_PATH = Path(__file__).resolve().parents[1] / "daily_arxiv/check_stats.py"
spec = importlib.util.spec_from_file_location("check_stats", MODULE_PATH)
check_stats = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_stats)


class PublishedHistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "daily_arxiv").mkdir()
        (self.root / "data").mkdir()
        self.previous = Path.cwd()
        os.chdir(self.root / "daily_arxiv")
        self.addCleanup(os.chdir, self.previous)
        self.env = patch.dict(os.environ, {"ARXIV_RUN_DATE": "2026-10-25"})
        self.env.start()
        self.addCleanup(self.env.stop)

    def write(self, filename, *ids):
        path = self.root / "data" / filename
        path.write_text("".join(json.dumps({"id": i}) + "\n" for i in ids), encoding="utf-8")
        return path

    def run_dedup(self):
        with contextlib.redirect_stderr(io.StringIO()):
            return check_stats.perform_deduplication()

    def test_drill_repeated_on_three_days_is_skipped(self):
        self.write("2026-10-23_AI_enhanced_Chinese.jsonl", "2610.30814")
        self.write("2026-10-24_AI_enhanced_Chinese.jsonl", "2610.30814")
        today = self.write("2026-10-25.jsonl", "2610.30814")
        self.assertEqual("no_new_content", self.run_dedup())
        self.assertFalse(today.exists())

    def test_history_older_than_seven_days_is_still_deduplicated(self):
        self.write("2026-10-01_AI_enhanced_Chinese.jsonl", "old")
        today = self.write("2026-10-25.jsonl", "old", "new")
        self.assertEqual("has_new_content", self.run_dedup())
        self.assertEqual('{"id": "new"}\n', today.read_text(encoding="utf-8"))

    def test_raw_failed_papers_are_not_mistaken_for_published_history(self):
        self.write("2026-10-24.jsonl", "success", "failed")
        self.write("2026-10-24_AI_enhanced_Chinese.jsonl", "success")
        today = self.write("2026-10-25.jsonl", "success", "failed")
        self.assertEqual("has_new_content", self.run_dedup())
        self.assertEqual('{"id": "failed"}\n', today.read_text(encoding="utf-8"))

    def test_same_day_reruns_and_future_outputs_do_not_suppress_today(self):
        self.write("2026-10-25_AI_enhanced_Chinese.jsonl", "current")
        self.write("2026-10-26_AI_enhanced_Chinese.jsonl", "future")
        today = self.write("2026-10-25.jsonl", "current", "future")
        self.assertEqual("has_new_content", self.run_dedup())
        self.assertEqual(2, len(today.read_text(encoding="utf-8").splitlines()))

    def test_versions_urls_and_cross_category_repeats_are_one_paper(self):
        self.write("2026-10-23_AI_enhanced_Chinese.jsonl", "2610.30814v1")
        today = self.write("2026-10-25.jsonl", "https://arxiv.org/abs/2610.30814v2", "newv1", "newv2")
        self.assertEqual("has_new_content", self.run_dedup())
        self.assertEqual('{"id": "newv1"}\n', today.read_text(encoding="utf-8"))

    def test_unreadable_history_stops_instead_of_reprocessing_everything(self):
        self.write("2026-10-23_AI_enhanced_Chinese.jsonl").write_text("broken\n", encoding="utf-8")
        today = self.write("2026-10-25.jsonl", "new")
        self.assertEqual("error", self.run_dedup())
        self.assertTrue(today.exists())

    def test_first_run_without_history_keeps_new_papers(self):
        today = self.write("2026-10-25.jsonl", "new")
        self.assertEqual("has_new_content", self.run_dedup())
        self.assertTrue(today.exists())

    def test_legacy_arxiv_url_normalization(self):
        self.assertEqual("hep-th/9901001", check_stats.normalize_paper_id("https://arxiv.org/pdf/hep-th/9901001v3.pdf"))


if __name__ == "__main__":
    unittest.main()
