"""Regression tests: refactored engine must match the original algorithm."""

from __future__ import annotations

import hashlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from optimizer.engine import compress_image as engine_compress
from tests.reference_original import compress_image as original_compress

IMAGES_DIR = ROOT / "images"
SUPPORTED = (".jpg", ".jpeg", ".png", ".webp", ".heic")


def _file_digest(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def _sample_images() -> list[Path]:
    if not IMAGES_DIR.is_dir():
        return []
    return sorted(
        path
        for path in IMAGES_DIR.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED
    )


class RegressionTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.samples = _sample_images()
        if not cls.samples:
            raise unittest.SkipTest(f"No sample images found in {IMAGES_DIR}")

    def test_engine_matches_original_on_all_samples(self) -> None:
        mismatches: list[str] = []

        with tempfile.TemporaryDirectory(prefix="optimizer-regression-") as tmp:
            for sample in self.samples:
                original_out = os.path.join(tmp, f"original_{sample.stem}.webp")
                engine_out = os.path.join(tmp, f"engine_{sample.stem}.webp")

                original_size, original_quality = original_compress(
                    str(sample), original_out
                )
                engine_size, engine_quality = engine_compress(str(sample), engine_out)

                if original_quality != engine_quality:
                    mismatches.append(
                        f"{sample.name}: quality {engine_quality} != {original_quality}"
                    )
                if original_size != engine_size:
                    mismatches.append(
                        f"{sample.name}: size_kb {engine_size!r} != {original_size!r}"
                    )
                if _file_digest(original_out) != _file_digest(engine_out):
                    mismatches.append(f"{sample.name}: WebP bytes differ")

        self.assertEqual(mismatches, [], "\n".join(mismatches))

    def test_return_types_match_original_contract(self) -> None:
        sample = self.samples[0]
        with tempfile.TemporaryDirectory(prefix="optimizer-contract-") as tmp:
            out = os.path.join(tmp, "out.webp")
            size_kb, quality = engine_compress(str(sample), out)

            self.assertIsInstance(size_kb, float)
            self.assertIsInstance(quality, int)
            self.assertTrue(os.path.isfile(out))
            self.assertGreater(size_kb, 0)


if __name__ == "__main__":
    unittest.main()
