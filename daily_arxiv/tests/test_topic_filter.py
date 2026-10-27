import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "daily_arxiv/topic_filter.py"
spec = importlib.util.spec_from_file_location("topic_filter", MODULE)
topic_filter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(topic_filter)


class ApplicationFilterTests(unittest.TestCase):
    def setUp(self):
        self.engine = topic_filter.ApplicationFilter()

    def paper(self, title, summary="We propose and evaluate a new visual method."):
        return {"id": "paper", "title": title, "summary": summary, "categories": ["cs.CV"]}

    def test_explicit_applications_are_excluded(self):
        for title in [
            "Remote Sensing Change Captioning", "Tracking in Satellite Video",
            "DRA-SAM for Low-Altitude Drone Referring Segmentation",
            "Medical Image Segmentation with 3D Reconstruction",
            "Saga: an ontology for a multimodal patient world model",
            "Whole-Slide Image Survival Analysis", "EEG Visual Decoding",
            "SWIFT-Cell: Segmentation Without Iterative Flow Tracking",
            "Raman Spectra Analysis", "Physics-aware Photonic Inverse Design",
            "Beet-Root Detection on Edge Devices", "Identification of Fish Species",
            "Industrial Defect Inspection", "Thin Crack Segmentation",
            "Live Esports Commentary", "Wildlife Identification",
        ]:
            with self.subTest(title=title):
                self.assertTrue(self.engine.classify(self.paper(title))["exclude"])

    def test_core_spatial_and_general_cv_methods_survive(self):
        for title in [
            "UniWorld: A Unified World Model for Visual World Simulation",
            "WMEval: Evaluating Interactive World Models Grounded on Physical Simulations",
            "GeoLift: Grounding Video Spatial Reasoning in Explicit 3D Maps",
            "Feed-forward 3D Reconstruction", "Gaussian Splatting for Scene Understanding",
            "Vision-Language-Action Models for Robotic Manipulation",
            "Interpreting Physics in Video Encoders",
            "Spectra: Implicit Decomposition for Deepfake Detection",
            "Gauge-Invariant Spectral Descriptors for Text-to-B-rep Retrieval",
            "Improving VLMs on Scientific Chart Analysis",
            "Generic Diffusion Models for Image Editing",
        ]:
            with self.subTest(title=title):
                self.assertFalse(self.engine.classify(self.paper(title))["exclude"])

    def test_aerial_navigation_is_not_confused_with_remote_sensing_analysis(self):
        paper = self.paper("SP-Guide: Aerial Vision-Language Navigation with Spatial Priors",
                           "Aerial imagery and remote sensing datasets are used to test navigation.")
        self.assertFalse(self.engine.classify(paper)["exclude"])

    def test_application_focus_in_abstract_is_detected(self):
        decision = self.engine.classify(self.paper("SWIFT: Faster Region Models",
                            "Cellpose outlines cells across stains in microscopy images."))
        self.assertTrue(decision["exclude"])
        self.assertEqual("abstract_intro", decision["field"])

    def test_a_domain_used_as_an_example_does_not_exclude_a_general_method(self):
        paper = self.paper("General Visual Representation Learning",
                           "We introduce a general method. We evaluate on medical and satellite imagery.")
        self.assertFalse(self.engine.classify(paper)["exclude"])

    def test_tensor_recovery_is_kept_when_hyperspectral_is_one_of_several_tasks(self):
        paper = self.paper("Shared-Factor-Conditioned Diffusion for Low-Rank Tensor Recovery",
                           "We unify tensor completion, denoising, and hyperspectral super-resolution.")
        self.assertFalse(self.engine.classify(paper)["exclude"])

    def test_disabled_filter_and_unknown_topics_keep_records_unchanged(self):
        papers = [self.paper("Medical Image Segmentation"), {"id": "unknown"}]
        kept, excluded = self.engine.filter(papers, enabled=False)
        self.assertEqual(papers, kept)
        self.assertEqual([], excluded)
        self.assertFalse(self.engine.classify({"id": "unknown"})["exclude"])

    def test_enable_environment_is_explicit(self):
        for value, enabled in [("true", True), ("false", False), ("0", False), ("", True)]:
            with patch.dict(os.environ, {"PAPER_FILTER_ENABLED": value}):
                self.assertEqual(enabled, topic_filter.enabled_from_environment())
        with patch.dict(os.environ, {"PAPER_FILTER_ENABLED": "typo"}):
            with self.assertRaises(ValueError):
                topic_filter.enabled_from_environment()

    def test_audit_preserves_full_excluded_paper_before_replacing_input(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "2026-10-27.jsonl"
            rejected = self.paper("Medical Image Segmentation")
            rejected["authors"] = ["Original author"]
            retained = {**self.paper("Interactive World Models"), "id": "kept"}
            source.write_text("\n".join(json.dumps(p) for p in [rejected, retained]) + "\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                report = topic_filter.filter_file(source, root / "reports")
            self.assertEqual([retained], [json.loads(l) for l in source.read_text(encoding="utf-8").splitlines()])
            saved = json.loads(next((root / "reports").glob("*.json")).read_text(encoding="utf-8"))
            self.assertEqual(rejected, saved["excluded"][0]["paper"])
            self.assertEqual(1, report["excluded_count"])
            with contextlib.redirect_stdout(io.StringIO()):
                topic_filter.filter_file(source, root / "reports")
            self.assertEqual(2, len(list((root / "reports").glob("*.json"))))

    def test_report_failure_leaves_source_untouched(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "2026-10-27.jsonl"
            original = json.dumps(self.paper("Medical Image Segmentation")) + "\n"
            source.write_text(original, encoding="utf-8")
            with patch.object(topic_filter, "atomic_write", side_effect=OSError("disk unavailable")):
                with self.assertRaises(OSError):
                    topic_filter.filter_file(source, root / "reports")
            self.assertEqual(original, source.read_text(encoding="utf-8"))

    def test_all_excluded_is_a_valid_empty_input_for_the_existing_no_data_gate(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            (root / "daily_arxiv").mkdir()
            source = root / "data/2026-10-27.jsonl"
            source.write_text(json.dumps(self.paper("Medical Image Segmentation")) + "\n", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                report = topic_filter.filter_file(source, root / "reports")
            self.assertEqual("", source.read_text(encoding="utf-8"))
            self.assertEqual(0, report["kept_count"])
            gate_spec = importlib.util.spec_from_file_location("filter_check_stats", MODULE.with_name("check_stats.py"))
            gate = importlib.util.module_from_spec(gate_spec)
            gate_spec.loader.exec_module(gate)
            previous = Path.cwd()
            try:
                os.chdir(root / "daily_arxiv")
                with patch.dict(os.environ, {"ARXIV_RUN_DATE": "2026-10-27"}), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as stopped:
                        gate.main()
                    self.assertEqual(1, stopped.exception.code)  # Existing workflow maps 1 to a normal skip.
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
