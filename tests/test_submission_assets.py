from __future__ import annotations

import struct
import unittest
from pathlib import Path
from xml.etree import ElementTree


ROOT = Path(__file__).resolve().parents[1]
SVG_PATH = ROOT / "docs" / "all-things-agentic-architecture.svg"
PNG_PATH = ROOT / "docs" / "all-things-agentic-architecture.png"


class SubmissionArchitectureAssetTests(unittest.TestCase):
    def test_vector_source_is_fixed_3_by_2_and_truthfully_scoped(self) -> None:
        root = ElementTree.parse(SVG_PATH).getroot()
        self.assertEqual(root.attrib.get("width"), "1800")
        self.assertEqual(root.attrib.get("height"), "1200")
        self.assertEqual(root.attrib.get("viewBox"), "0 0 1800 1200")

        source = SVG_PATH.read_text(encoding="utf-8")
        for required in (
            "Cloud Run API",
            "Cloud Tasks",
            "Firestore",
            "Cloud Run worker",
            "Cloud Storage",
            "Gemini 3.5 Flash",
            "Chirp 3 HD",
            "LIVE SHORT PASS · OWNER REVIEW PENDING",
            "Partial or malformed media is never",
            "published as success",
        ):
            self.assertIn(required, source)
        self.assertNotIn("LIVE-PROVEN", source)
        self.assertNotIn("LIVE PROOF PENDING", source)

    def test_rendered_png_is_exact_size_and_bounded(self) -> None:
        data = PNG_PATH.read_bytes()
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        width, height = struct.unpack(">II", data[16:24])
        self.assertEqual((width, height), (1800, 1200))
        self.assertLess(len(data), 5 * 1024 * 1024)

    def test_submission_docs_link_the_rendered_and_source_assets(self) -> None:
        architecture = (ROOT / "docs" / "ARCHITECTURE.md").read_text(
            encoding="utf-8"
        )
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        checklist = (ROOT / "docs" / "DEMO_SUBMISSION_CHECKLIST.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("all-things-agentic-architecture.png", architecture)
        self.assertIn("all-things-agentic-architecture.svg", architecture)
        self.assertIn("all-things-agentic-architecture.svg", readme)
        self.assertIn("all-things-agentic-architecture.png", checklist)


if __name__ == "__main__":
    unittest.main()
