"""Catalogue maintenance must hash ordinary files, not reuse Git blob IDs."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[2] / "packaging/models/refresh_catalogue.py"
spec = importlib.util.spec_from_file_location("refresh_catalogue", SCRIPT)
refresh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(refresh)


class RefreshCatalogueTests(unittest.TestCase):
    def test_lfs_uses_content_hash_without_downloading_weights(self):
        row = {"rfilename": "encoder.onnx", "blobId": "b" * 40,
               "lfs": {"size": 123, "sha256": "a" * 64}}
        with patch.object(refresh, "fetch") as fetch:
            self.assertEqual(refresh.file_metadata("owner/model", "revision", row),
                             (123, "a" * 64))
        fetch.assert_not_called()

    def test_ordinary_file_hashes_raw_bytes_at_metadata_revision(self):
        data = "中 1\n英 2\n".encode()
        row = {"rfilename": "tokens.txt", "blobId": "b" * 40, "size": len(data)}
        with patch.object(refresh, "fetch", return_value=data) as fetch:
            self.assertEqual(refresh.file_metadata("owner/model", "revision", row),
                             (len(data), hashlib.sha256(data).hexdigest()))
        fetch.assert_called_once_with(
            "https://huggingface.co/owner/model/resolve/revision/tokens.txt")

    def test_truncated_ordinary_file_is_rejected(self):
        with (
            patch.object(refresh, "fetch", return_value=b"short"),
            self.assertRaisesRegex(ValueError, "Unexpected file size"),
        ):
            refresh.file_metadata("owner/model", "revision",
                                  {"rfilename": "tokens.txt", "size": 100})

    def test_check_does_not_write_and_refresh_preserves_comments(self):
        original = '''# Keep this explanation.
models:
  - id: streaming
    files:
      - filename: tokens.txt
        repo: owner/model
        size: 99 # file bytes
        sha256: stale
'''
        data = b"token 0\n"
        doc = {"sha": "revision", "siblings": [
            {"rfilename": "tokens.txt", "size": len(data), "blobId": "b" * 40}]}

        def fetch(url):
            return json.dumps(doc).encode() if "/api/models/" in url else data

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "models.yaml"
            path.write_text(original)
            with patch.object(refresh, "fetch", side_effect=fetch):
                self.assertEqual(refresh.main(["--check", "--catalogue", str(path)]), 1)
                self.assertEqual(path.read_text(), original)
                self.assertEqual(refresh.main(["--catalogue", str(path)]), 0)
                expected = original.replace("size: 99", f"size: {len(data)}").replace(
                    "sha256: stale", f"sha256: {hashlib.sha256(data).hexdigest()}")
                self.assertEqual(path.read_text(), expected)
                self.assertEqual(refresh.main(["--check", "--catalogue", str(path)]), 0)


if __name__ == "__main__":
    unittest.main()
