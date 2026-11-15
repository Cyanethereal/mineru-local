"""Tests for MinerUArtifactManifest schema version 1."""
from __future__ import annotations

import hashlib
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve()
SCRIPT_DIR = HERE.parent.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import mineru_manifest as mm  # noqa: E402


def execution() -> dict:
    return {
        "environment": "wsl",
        "requested_environment": "wsl",
        "backend": "pipeline",
        "method": "auto",
        "io_mode": "staged",
        "wsl_distro": "TestDistro",
        "mineru_version": "3.4.4",
        "executable": "/opt/mineru/bin/mineru",
        "command": "mineru -p input -o output",
        "returncode": 0,
        "fallback": {"used": False, "smoke_attempts": []},
        "config_sources": {},
        "stdout_tail": "",
        "stderr_tail": "",
    }


class ManifestBuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.input = self.root / "lecture.pdf"
        self.input.write_bytes(b"%PDF-1.4 sample")
        self.output = self.root / "raw"
        self.output.mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def test_records_primary_assets_and_hashes(self):
        doc = self.output / "lecture" / "auto"
        assets = doc / "images"
        assets.mkdir(parents=True)
        markdown = doc / "lecture.md"
        image = assets / "figure.png"
        image.write_bytes(b"PNG")
        markdown.write_text("# Lecture\n\n![figure](images/figure.png)\n", encoding="utf-8")
        (assets / "unused.png").write_bytes(b"UNUSED")
        (self.output / "content_list.json").write_text("[]", encoding="utf-8")

        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
        )
        manifest_path = mm.write_manifest(manifest, self.output)
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))

        self.assertEqual(saved["manifest_schema_version"], 1)
        self.assertEqual(saved["status"], "ok")
        self.assertEqual(saved["markdown"]["selection"]["reason"], "sole_non_empty_markdown")
        self.assertEqual(saved["markdown"]["primary"]["path"], "lecture/auto/lecture.md")
        self.assertEqual(saved["input"]["sha256"], hashlib.sha256(self.input.read_bytes()).hexdigest())
        referenced = next(item for item in saved["assets"] if item["path"].endswith("figure.png"))
        unused = next(item for item in saved["assets"] if item["path"].endswith("unused.png"))
        self.assertTrue(referenced["referenced"])
        self.assertEqual(referenced["referenced_by"], ["lecture/auto/lecture.md"])
        self.assertFalse(unused["referenced"])
        self.assertTrue(any(item["code"] == "unreferenced_assets_present" for item in saved["warnings"]))
        self.assertTrue((self.output / mm.OUTPUT_TREE_FILENAME).is_file())
        self.assertEqual(saved["output_tree"]["path"], mm.OUTPUT_TREE_FILENAME)

    def test_empty_markdown_and_missing_primary_fail(self):
        (self.output / "empty.md").write_text("", encoding="utf-8")
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
        )
        self.assertEqual(manifest["status"], "failed")
        self.assertIsNone(manifest["markdown"]["primary"])
        self.assertTrue(any(item["code"] == "empty_markdown_file" for item in manifest["warnings"]))
        self.assertTrue(any(item["code"] == "no_non_empty_markdown" for item in manifest["errors"]))

    def test_unique_stem_match_resolves_multiple_markdown(self):
        for rel in ("lecture/auto/lecture.md", "lecture/auto/preview.md"):
            path = self.output / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(f"# {rel}", encoding="utf-8")
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
        )
        self.assertEqual(manifest["markdown"]["primary"]["path"], "lecture/auto/lecture.md")
        self.assertEqual(manifest["markdown"]["selection"]["reason"], "unique_input_stem_match")
        self.assertEqual(manifest["status"], "ok")

    def test_ambiguous_markdown_is_explicit_error(self):
        for rel in ("a/other.md", "b/other.md"):
            path = self.output / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("# Other", encoding="utf-8")
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
        )
        self.assertEqual(manifest["status"], "partial")
        self.assertIsNone(manifest["markdown"]["primary"])
        self.assertTrue(any(item["code"] == "ambiguous_primary_markdown" for item in manifest["errors"]))

    def test_all_artifact_paths_are_relative_and_safe(self):
        doc = self.output / "lecture" / "auto"
        doc.mkdir(parents=True)
        (doc / "lecture.md").write_text("![x](../../outside.png)", encoding="utf-8")
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
        )
        paths = [manifest["markdown"]["primary"]["path"]]
        paths.extend(item["path"] for item in manifest["assets"])
        for value in paths:
            self.assertFalse(value.startswith("/"))
            self.assertNotIn("..", Path(value).parts)

    def test_stderr_warning_sample_is_captured(self):
        doc = self.output / "lecture" / "auto"
        doc.mkdir(parents=True)
        (doc / "lecture.md").write_text("# Lecture", encoding="utf-8")
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
            stderr="WARNING: low resolution image\n",
        )
        self.assertTrue(any(item["code"] == "stderr_warning_sample" for item in manifest["warnings"]))

    def test_recoverable_stderr_error_is_warning_on_success(self):
        doc = self.output / "lecture" / "auto"
        doc.mkdir(parents=True)
        (doc / "lecture.md").write_text("# Lecture", encoding="utf-8")
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
            stderr="(EngineCore) Failed to get device capability: SM 12.x requires CUDA >= 12.9.\n",
        )
        self.assertEqual(manifest["status"], "ok")
        self.assertEqual(manifest["errors"], [])
        self.assertTrue(any(item["code"] == "stderr_error_sample" for item in manifest["warnings"]))

    def test_stderr_error_is_fatal_on_nonzero_exit(self):
        doc = self.output / "lecture" / "auto"
        doc.mkdir(parents=True)
        (doc / "lecture.md").write_text("# Lecture", encoding="utf-8")
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution={**execution(), "returncode": 2},
            returncode=2,
            stderr="FATAL: model failed\n",
        )
        self.assertEqual(manifest["status"], "partial")
        self.assertTrue(any(item["code"] == "stderr_error_sample" for item in manifest["errors"]))
        self.assertTrue(any(item["code"] == "mineru_returncode_nonzero" for item in manifest["errors"]))

    def test_duplicate_stderr_samples_are_deduplicated(self):
        doc = self.output / "lecture" / "auto"
        doc.mkdir(parents=True)
        (doc / "lecture.md").write_text("# Lecture", encoding="utf-8")
        duplicate = "Failed to get device capability: SM 12.x requires CUDA >= 12.9.\n"
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
            stderr=duplicate + duplicate,
        )
        samples = [x for x in manifest["warnings"] if x["code"] == "stderr_error_sample"]
        self.assertEqual(len(samples), 1)

    def test_schema_relative_path_pattern_rejects_unsafe_values(self):
        schema = json.loads(
            (SCRIPT_DIR / "schemas" / "mineru-artifact-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        pattern = schema["$defs"]["relative_path"]["pattern"]
        unsafe = [
            "/absolute/path.png",
            "C:/drive/path.png",
            r"C:\drive\path.png",
            "folder\\asset.png",
            r"\\server\share\asset.png",
            "folder//asset.png",
            "./../asset.png",
            "folder/../asset.png",
            "folder/..",
        ]
        safe = [
            "folder/asset.png",
            "folder/a..b/asset.png",
            "folder/..../asset.png",
        ]
        for value in unsafe:
            with self.subTest(value=value):
                self.assertIsNone(re.search(pattern, value))
        for value in safe:
            with self.subTest(value=value):
                self.assertIsNotNone(re.search(pattern, value))

    def test_generated_manifest_validates_against_schema_v1(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema is not installed")
        doc = self.output / "lecture" / "auto"
        doc.mkdir(parents=True)
        (doc / "lecture.md").write_text("# Lecture", encoding="utf-8")
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
        )
        manifest_path = mm.write_manifest(manifest, self.output)
        saved = json.loads(manifest_path.read_text(encoding="utf-8"))
        schema = json.loads(
            (SCRIPT_DIR / "schemas" / "mineru-artifact-manifest.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator(schema).validate(saved)

    def test_missing_and_out_of_root_references_are_errors(self):
        doc = self.output / "lecture" / "auto"
        doc.mkdir(parents=True)
        (doc / "lecture.md").write_text(
            "![missing](missing.png)\n"
            "![outside](../../../outside.png)\n"
            "![unc](//server/share/image.png)\n",
            encoding="utf-8",
        )
        (self.root / "outside.png").write_bytes(b"OUTSIDE")
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
        )
        codes = {item["code"] for item in manifest["errors"]}
        self.assertIn("asset_reference_missing", codes)
        self.assertIn("asset_reference_outside_root", codes)
        self.assertEqual(manifest["status"], "partial")
        warning_codes = {item["code"] for item in manifest["warnings"]}
        self.assertNotIn("asset_reference_missing", warning_codes)
        self.assertNotIn("asset_reference_outside_root", warning_codes)

    def test_referenced_non_image_file_is_listed_as_asset(self):
        doc = self.output / "lecture" / "auto"
        doc.mkdir(parents=True)
        notes = doc / "notes.txt"
        notes.write_text("supplement", encoding="utf-8")
        (doc / "lecture.md").write_text(
            "# Lecture\n\n[supplement](notes.txt)\n", encoding="utf-8"
        )
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
        )
        asset = next(item for item in manifest["assets"] if item["path"].endswith("notes.txt"))
        self.assertTrue(asset["referenced"])
        self.assertEqual(asset["referenced_by"], ["lecture/auto/lecture.md"])
        self.assertEqual(asset["sha256"], hashlib.sha256(notes.read_bytes()).hexdigest())
        self.assertNotIn(
            "lecture/auto/notes.txt", {item["path"] for item in manifest["other_files"]}
        )

    def test_stdout_error_is_recoverable_warning_on_success(self):
        doc = self.output / "lecture" / "auto"
        doc.mkdir(parents=True)
        (doc / "lecture.md").write_text("# Lecture", encoding="utf-8")
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
            stdout="ERROR: transient stdout issue\nWARNING: deprecated option\n",
        )
        self.assertEqual(manifest["status"], "ok")
        self.assertEqual(manifest["errors"], [])
        warning_codes = {item["code"] for item in manifest["warnings"]}
        self.assertIn("stdout_error_sample", warning_codes)
        self.assertIn("stdout_warning_sample", warning_codes)

    def test_nonzero_exit_blocks_stdout_and_stderr_error_samples(self):
        doc = self.output / "lecture" / "auto"
        doc.mkdir(parents=True)
        (doc / "lecture.md").write_text("# Lecture", encoding="utf-8")
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution={**execution(), "returncode": 9},
            returncode=9,
            stdout="ERROR: stdout failure\n",
            stderr="FATAL: stderr failure\n",
        )
        error_codes = {item["code"] for item in manifest["errors"]}
        self.assertIn("stdout_error_sample", error_codes)
        self.assertIn("stderr_error_sample", error_codes)
        self.assertIn("mineru_returncode_nonzero", error_codes)

    def test_precomputed_input_hash_is_preserved(self):
        doc = self.output / "lecture" / "auto"
        doc.mkdir(parents=True)
        (doc / "lecture.md").write_text("# Lecture", encoding="utf-8")
        supplied_hash = "a" * 64
        manifest = mm.build_manifest(
            input_path=self.input,
            output_root=self.output,
            execution=execution(),
            returncode=0,
            input_sha256=supplied_hash,
            input_size_bytes=123,
        )
        self.assertEqual(manifest["input"]["sha256"], supplied_hash)
        self.assertEqual(manifest["input"]["size_bytes"], 123)

    def test_schema_file_is_valid_json(self):
        schema = SCRIPT_DIR / "schemas" / "mineru-artifact-manifest.schema.json"
        payload = json.loads(schema.read_text(encoding="utf-8"))
        self.assertEqual(payload["properties"]["manifest_schema_version"]["const"], 1)


if __name__ == "__main__":
    unittest.main()
