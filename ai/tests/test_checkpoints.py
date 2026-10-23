import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from checkpoints import CheckpointStore


class CheckpointStoreTests(unittest.TestCase):
    def test_corrupt_record_is_ignored(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CheckpointStore(directory, {"model": "test"})
            key = store.key({"id": "one"})
            path = Path(directory) / f"{key}.json"
            for content in ('{"key":', "[]", "null"):
                path.write_text(content, encoding="utf-8")
                with contextlib.redirect_stderr(io.StringIO()):
                    self.assertIsNone(store.load(key))

    def test_interrupted_atomic_replace_keeps_previous_record(self):
        with tempfile.TemporaryDirectory() as directory:
            store = CheckpointStore(directory, {"model": "test"})
            key = store.key({"id": "one"})
            store.save(key, {"id": "one", "AI": {"tldr": "Original"}})
            with patch("checkpoints.os.replace", side_effect=OSError("Disk failure")):
                with self.assertRaises(OSError):
                    store.save(key, {"id": "one", "AI": {"tldr": "New"}})
            self.assertEqual("Original", store.load(key)["AI"]["tldr"])
            self.assertEqual([], list(Path(directory).glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
