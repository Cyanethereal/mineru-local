from __future__ import annotations

from pathlib import Path
import unittest


ROOT = Path(__file__).resolve().parents[2]


class MarkdownEncodingTests(unittest.TestCase):
    def test_human_markdown_is_utf8_bom(self):
        paths = [ROOT / "README.md", *sorted((ROOT / "references").glob("*.md"))]
        for path in paths:
            with self.subTest(path=path.name):
                data = path.read_bytes()
                self.assertTrue(data.startswith(b"\xef\xbb\xbf"), path)
                data[3:].decode("utf-8")

    def test_skill_md_is_utf8_without_bom(self):
        data = (ROOT / "SKILL.md").read_bytes()
        self.assertFalse(data.startswith(b"\xef\xbb\xbf"))
        data.decode("utf-8")


if __name__ == "__main__":
    unittest.main()