"""Atomic, per-paper checkpoints for resumable AI enhancement."""

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


class CheckpointStore:
    def __init__(self, directory, context):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.context = context

    def key(self, paper):
        payload = {"version": 1, "context": self.context, "paper": paper}
        encoded = json.dumps(
            payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    def load(self, key):
        path = self.directory / f"{key}.json"
        if not path.exists():
            return None
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if record.get("key") != key or not isinstance(record.get("paper"), dict):
                raise ValueError("Invalid checkpoint record")
            return record["paper"]
        except (OSError, ValueError, AttributeError) as error:
            print(f"Ignoring unreadable checkpoint {path.name}: {error}", file=sys.stderr)
            return None

    def save(self, key, paper):
        """Flush a complete record before atomically replacing its checkpoint."""
        temporary_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=self.directory,
                suffix=".tmp", delete=False
            ) as stream:
                temporary_path = Path(stream.name)
                json.dump({"key": key, "paper": paper}, stream, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_path, self.directory / f"{key}.json")
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
