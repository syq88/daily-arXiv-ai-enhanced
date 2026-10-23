import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch


# enhance.py loads its prompt files relative to the working directory.
AI_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AI_DIR))
previous_directory = Path.cwd()
try:
    os.chdir(AI_DIR)
    import enhance
finally:
    os.chdir(previous_directory)


class PaperEnhancementTests(unittest.TestCase):
    def run_batch(self, papers, worker):
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch.object(enhance, "ChatOpenAI"),
            patch.object(enhance.ChatPromptTemplate, "from_messages"),
            patch.object(enhance, "process_single_item", side_effect=worker),
            contextlib.redirect_stderr(io.StringIO()),
        ):
            return enhance.process_all_items(
                papers, "test-model", "Chinese", max_workers=1
            )

    def test_partial_failure_keeps_other_papers_in_input_order(self):
        papers = [{"id": "first"}, {"id": "broken"}, {"id": "last"}]

        def worker(chain, item, language):
            if item["id"] == "broken":
                raise RuntimeError("No structured response")
            return {**item, "AI": {"tldr": "A summary"}}

        result = self.run_batch(papers, worker)
        self.assertEqual(["first", "last"], [p["id"] for p in result])

    def test_all_failed_papers_still_fail_the_batch(self):
        def worker(chain, item, language):
            raise RuntimeError("401 Unauthorized")

        with self.assertRaisesRegex(RuntimeError, r"2 paper\(s\) failed"):
            self.run_batch([{"id": "first"}, {"id": "second"}], worker)

    def test_filtered_paper_does_not_hide_a_fully_failed_batch(self):
        def worker(chain, item, language):
            if item["id"] == "filtered":
                return None
            raise RuntimeError("No structured response")

        with self.assertRaisesRegex(RuntimeError, r"1 paper\(s\) failed"):
            self.run_batch([{"id": "filtered"}, {"id": "broken"}], worker)

    def test_filtered_papers_are_omitted_from_successful_batch(self):
        def worker(chain, item, language):
            return None if item["id"] == "filtered" else item

        result = self.run_batch([{"id": "filtered"}, {"id": "ok"}], worker)
        self.assertEqual([{"id": "ok"}], result)

    @patch.object(enhance, "is_sensitive", return_value=False)
    def test_none_response_becomes_a_clear_per_paper_error(self, _filter):
        chain = MagicMock()
        chain.invoke.return_value = None
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, "AI request failed for broken") as caught:
                enhance.process_single_item(
                    chain, {"id": "broken", "summary": "An abstract"}, "Chinese"
                )
        self.assertIsInstance(caught.exception.__cause__, ValueError)
        self.assertIn("no structured summary", str(caught.exception.__cause__))

    def test_main_writes_successful_results_after_partial_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "papers.jsonl"
            source.write_text(
                '{"id": "first"}\n{"id": "broken"}\n{"id": "last"}\n',
                encoding="utf-8",
            )

            def worker(chain, item, language):
                if item["id"] == "broken":
                    raise RuntimeError("No structured response")
                return {**item, "AI": {"tldr": "A summary"}}

            with (
                patch.object(sys, "argv", ["enhance.py", "--data", str(source)]),
                patch.dict(
                    os.environ,
                    {"OPENAI_API_KEY": "test-key", "MODEL_NAME": "test-model", "LANGUAGE": "Chinese"},
                ),
                patch.object(enhance, "ChatOpenAI"),
                patch.object(enhance.ChatPromptTemplate, "from_messages"),
                patch.object(enhance, "process_single_item", side_effect=worker),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                enhance.main()

            output = source.with_name("papers_AI_enhanced_Chinese.jsonl")
            saved = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(["first", "last"], [paper["id"] for paper in saved])

    def test_resume_retries_only_failed_papers_and_saves_progress_immediately(self):
        with tempfile.TemporaryDirectory() as directory:
            papers = [{"id": "first"}, {"id": "broken"}, {"id": "last"}]
            result_fields = {
                name: "A summary"
                for name in ("tldr", "motivation", "method", "result", "conclusion")
            }
            checkpoint_directory = Path(directory)
            observed = []

            def first_worker(chain, item, language):
                # With one worker, the second call starts only after the first
                # returns; the main thread may still be saving that first result.
                if item["id"] == "broken":
                    raise RuntimeError("Temporary failure")
                return {**item, "AI": result_fields}

            def on_result(item):
                saved = list(checkpoint_directory.glob("*.json"))
                self.assertTrue(saved, "Checkpoint must exist before publishing progress")
                observed.append(item["id"])

            common_patches = {
                "OPENAI_API_KEY": "test-key",
                "OPENAI_BASE_URL": "https://example.invalid/v1",
            }
            with (
                patch.dict(os.environ, common_patches),
                patch.object(enhance, "ChatOpenAI"),
                patch.object(enhance.ChatPromptTemplate, "from_messages"),
                patch.object(enhance, "process_single_item", side_effect=first_worker),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = enhance.process_all_items(
                    papers, "test-model", "Chinese", 1,
                    checkpoint_dir=directory, on_result=on_result,
                )

            self.assertEqual(["first", "last"], [p["id"] for p in result])
            self.assertCountEqual(["first", "last"], observed)
            self.assertEqual(2, len(list(checkpoint_directory.glob("*.json"))))

            def recovered_worker(chain, item, language):
                return {**item, "AI": result_fields}

            with (
                patch.dict(os.environ, common_patches),
                patch.object(enhance, "ChatOpenAI"),
                patch.object(enhance.ChatPromptTemplate, "from_messages"),
                patch.object(enhance, "process_single_item", side_effect=recovered_worker) as worker,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = enhance.process_all_items(
                    papers, "test-model", "Chinese", 1, checkpoint_dir=directory
                )

            self.assertEqual(["first", "broken", "last"], [p["id"] for p in result])
            self.assertEqual(1, worker.call_count)
            self.assertEqual("broken", worker.call_args.args[1]["id"])

    def test_restart_after_interruption_reuses_flushed_checkpoint(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = {"id": "done"}
            completed = {
                **paper,
                "AI": {name: "Done" for name in ("tldr", "motivation", "method", "result", "conclusion")},
            }

            def stop_after_save(item):
                raise KeyboardInterrupt("Simulated stopped job")

            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
                patch.object(enhance, "ChatOpenAI"),
                patch.object(enhance.ChatPromptTemplate, "from_messages"),
                patch.object(enhance, "process_single_item", return_value=completed),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                with self.assertRaises(KeyboardInterrupt):
                    enhance.process_all_items(
                        [paper], "test-model", "Chinese", 1,
                        checkpoint_dir=directory, on_result=stop_after_save,
                    )

            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
                patch.object(enhance, "ChatOpenAI"),
                patch.object(enhance.ChatPromptTemplate, "from_messages"),
                patch.object(enhance, "process_single_item") as worker,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                result = enhance.process_all_items(
                    [paper], "test-model", "Chinese", 1, checkpoint_dir=directory
                )
                worker.assert_not_called()
                self.assertEqual([completed], result)

    def test_invalid_checkpoint_is_reprocessed(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = {"id": "one"}
            completed = {
                **paper,
                "AI": {name: "Done" for name in ("tldr", "motivation", "method", "result", "conclusion")},
            }
            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
                patch.object(enhance, "ChatOpenAI"),
                patch.object(enhance.ChatPromptTemplate, "from_messages"),
                patch.object(enhance, "process_single_item", return_value=completed) as worker,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                enhance.process_all_items(
                    [paper], "test-model", "Chinese", 1, checkpoint_dir=directory
                )
                cache_file = next(Path(directory).glob("*.json"))
                record = json.loads(cache_file.read_text(encoding="utf-8"))
                record["paper"]["AI"] = None
                cache_file.write_text(json.dumps(record), encoding="utf-8")
                worker.reset_mock()
                enhance.process_all_items(
                    [paper], "test-model", "Chinese", 1, checkpoint_dir=directory
                )
                self.assertEqual(1, worker.call_count)

    def test_changed_model_prompt_or_abstract_does_not_reuse_summary(self):
        with tempfile.TemporaryDirectory() as directory:
            paper = {"id": "one", "summary": "Original abstract"}
            fields = {name: "Done" for name in ("tldr", "motivation", "method", "result", "conclusion")}

            def worker(chain, item, language):
                return {**item, "AI": fields}

            with (
                patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
                patch.object(enhance, "ChatOpenAI"),
                patch.object(enhance.ChatPromptTemplate, "from_messages"),
                patch.object(enhance, "process_single_item", side_effect=worker) as mock_worker,
                contextlib.redirect_stderr(io.StringIO()),
            ):
                enhance.process_all_items([paper], "model-a", "Chinese", 1, checkpoint_dir=directory)
                mock_worker.reset_mock()
                enhance.process_all_items([paper], "model-b", "Chinese", 1, checkpoint_dir=directory)
                self.assertEqual(1, mock_worker.call_count)

                mock_worker.reset_mock()
                with patch.object(enhance, "system", enhance.system + " Modified"):
                    enhance.process_all_items([paper], "model-a", "Chinese", 1, checkpoint_dir=directory)
                self.assertEqual(1, mock_worker.call_count)

                mock_worker.reset_mock()
                enhance.process_all_items(
                    [{**paper, "summary": "Updated abstract"}],
                    "model-a", "Chinese", 1, checkpoint_dir=directory,
                )
                self.assertEqual(1, mock_worker.call_count)


if __name__ == "__main__":
    unittest.main()
