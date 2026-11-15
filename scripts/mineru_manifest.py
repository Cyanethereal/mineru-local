"""Build the public MinerUArtifactManifest (schema version 1).

The manifest is deliberately self-contained: all artifact paths are POSIX
paths relative to the manifest directory. It records hashes for the input,
Markdown candidates and observed assets, plus the parse environment and any
warnings/errors needed by downstream curation.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import mimetypes
import os
import re
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any, Optional

MANIFEST_SCHEMA_VERSION = 1
MANIFEST_FILENAME = "mineru-artifact-manifest.json"
OUTPUT_TREE_FILENAME = "mineru-output-tree.txt"
TOOL_VERSION = "0.3.0"

IMAGE_SUFFIXES = {
    ".png", ".jpg", ".jpeg", ".jp2", ".webp", ".gif", ".bmp", ".svg", ".tif", ".tiff"
}
MARKDOWN_IMAGE_RE = re.compile(
    r"!\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[\"'][^)]*[\"'])?\s*\)",
    re.IGNORECASE,
)
MARKDOWN_LINK_RE = re.compile(
    r"(?<!!)\[[^\]]*\]\(\s*(?:<([^>]+)>|([^\s)]+))(?:\s+[\"'][^)]*[\"'])?\s*\)",
    re.IGNORECASE,
)
HTML_RESOURCE_RE = re.compile(
    r"<(?:img|a|source|embed|object)\b[^>]*\b(?:src|href|data)\s*=\s*[\"']([^\"']+)[\"']",
    re.IGNORECASE,
)
DRIVE_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")
WARNING_LINE_RE = re.compile(r"\b(warn(?:ing)?|deprecated|fallback)\b", re.IGNORECASE)
ERROR_LINE_RE = re.compile(r"\b(error|failed|fatal|exception|traceback)\b", re.IGNORECASE)


def utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def relative_posix(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _safe_relative(root: Path, path: Path) -> str:
    try:
        return relative_posix(path, root)
    except ValueError as exc:
        raise ValueError(f"artifact escapes output root: {path}") from exc


def _line_count(path: Path) -> Optional[int]:
    try:
        return len(path.read_text(encoding="utf-8", errors="replace").splitlines())
    except OSError:
        return None


def _file_record(root: Path, path: Path, *, include_line_count: bool = False) -> dict[str, Any]:
    record: dict[str, Any] = {
        "path": _safe_relative(root, path),
        "size_bytes": path.stat().st_size,
        "sha256": sha256_file(path),
    }
    if include_line_count:
        record["line_count"] = _line_count(path)
    return record


def _iter_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def build_output_tree(root: Path, *, output_path: Path) -> dict[str, Any]:
    """Write a deterministic text snapshot and return its metadata."""
    entries: list[str] = []
    for path in sorted(root.rglob("*"), key=lambda p: p.relative_to(root).as_posix()):
        if path == output_path or path.name in {MANIFEST_FILENAME, OUTPUT_TREE_FILENAME}:
            continue
        rel = _safe_relative(root, path)
        if path.is_dir():
            entries.append(f"d\t{rel}")
        elif path.is_file():
            entries.append(f"f\t{rel}\t{path.stat().st_size}")
    text = "# kind\trelative_path\tsize_bytes\n" + "\n".join(entries) + "\n"
    output_path.write_text(text, encoding="utf-8", newline="\n")
    return {
        "path": _safe_relative(root, output_path),
        "sha256": sha256_text(text),
        "entry_count": len(entries),
        "excludes": [MANIFEST_FILENAME, OUTPUT_TREE_FILENAME],
    }


def _extract_document_references(markdown_text: str) -> list[str]:
    refs: list[str] = []
    for match in MARKDOWN_IMAGE_RE.finditer(markdown_text):
        refs.append(match.group(1) or match.group(2))
    for match in MARKDOWN_LINK_RE.finditer(markdown_text):
        refs.append(match.group(1) or match.group(2))
    refs.extend(match.group(1) for match in HTML_RESOURCE_RE.finditer(markdown_text))
    # Preserve first-seen order while avoiding duplicate diagnostics.
    return list(dict.fromkeys(refs))


def _resolve_asset_reference(
    markdown_rel: str, raw_ref: str, root: Path
) -> tuple[Optional[str], Optional[str]]:
    """Resolve a local Markdown reference inside the output root.

    Returns ``(relative_path, reason)``. ``reason`` is one of
    ``outside_output_root`` or ``invalid_reference`` for local references that
    cannot safely be manifested. External URLs and inert references are
    intentionally ignored with ``(None, None)``.
    """
    ref = raw_ref.strip().strip("<>")
    if not ref or ref.startswith(("#", "data:", "mailto:", "tel:")):
        return None, None
    if DRIVE_PATH_RE.match(ref):
        return None, "outside_output_root"
    if ref.startswith("\\\\") or ref.startswith("//"):
        return None, "outside_output_root"

    parsed = urllib.parse.urlparse(ref)
    scheme = parsed.scheme.lower()
    if scheme:
        if scheme != "file":
            return None, None
        if parsed.netloc and parsed.netloc.lower() not in ("", "localhost"):
            return None, "outside_output_root"
        raw_path = urllib.parse.unquote(parsed.path)
    else:
        # Protocol-relative URLs are external resources, not output assets.
        if parsed.netloc:
            return None, None
        raw_path = urllib.parse.unquote(parsed.path)
    if not raw_path:
        return None, None

    # Treat Markdown-style Windows separators consistently across host OSes.
    raw_path = raw_path.replace("\\", "/")
    candidate = (root / markdown_rel).parent.joinpath(raw_path)
    try:
        resolved = candidate.resolve(strict=False)
        rel = resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None, "outside_output_root"
    except OSError:
        return None, "invalid_reference"
    return rel, None


def _collect_asset_relations(
    root: Path, markdown_records: list[dict[str, Any]]
) -> tuple[
    dict[str, set[str]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    relations: dict[str, set[str]] = {}
    missing: list[dict[str, Any]] = []
    outside: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    for record in markdown_records:
        md_rel = record["path"]
        md_path = root / md_rel
        try:
            text = md_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for raw_ref in _extract_document_references(text):
            resolved_rel, reason = _resolve_asset_reference(md_rel, raw_ref, root)
            detail = {"reference": raw_ref, "referenced_by": md_rel}
            if reason == "outside_output_root":
                outside.append(detail)
                continue
            if reason == "invalid_reference":
                invalid.append(detail)
                continue
            if resolved_rel is None:
                continue
            if (root / resolved_rel).is_file():
                relations.setdefault(resolved_rel, set()).add(md_rel)
            else:
                missing.append({
                    **detail,
                    "resolved_path": resolved_rel,
                })
    return relations, missing, outside, invalid


def _markdown_candidates(root: Path, input_stem: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    records: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for path in _iter_files(root):
        if path.suffix.lower() != ".md":
            continue
        record = _file_record(root, path, include_line_count=True)
        if record["size_bytes"] == 0:
            warnings.append({
                "code": "empty_markdown_file",
                "message": f"empty Markdown file ignored: {record['path']}",
                "details": {"path": record["path"]},
            })
            continue
        records.append(record)

    selection: dict[str, Any] = {
        "rule": "mineru-artifact-manifest/v1",
        "primary_path": None,
        "reason": None,
    }
    if not records:
        selection["reason"] = "no_non_empty_markdown"
        return records, warnings, selection

    records.sort(key=lambda item: (item["path"], item["size_bytes"], item["sha256"]))
    if len(records) == 1:
        primary = records[0]
        selection["reason"] = "sole_non_empty_markdown"
    else:
        stem_matches = [item for item in records if Path(item["path"]).stem == input_stem]
        if len(stem_matches) == 1:
            primary = stem_matches[0]
            selection["reason"] = "unique_input_stem_match"
        else:
            depth = min(len(Path(item["path"]).parts) for item in records)
            shallowest = [item for item in records if len(Path(item["path"]).parts) == depth]
            if len(shallowest) == 1:
                primary = shallowest[0]
                selection["reason"] = "unique_shallowest_markdown"
            else:
                primary = None
                selection["reason"] = "ambiguous_primary_markdown"
        warnings.append({
            "code": "multiple_markdown_candidates",
            "message": f"observed {len(records)} non-empty Markdown candidates",
            "details": {"candidates": [item["path"] for item in records]},
        })
    if primary is not None:
        selection["primary_path"] = primary["path"]
    for record in records:
        record["is_primary"] = record["path"] == selection["primary_path"]
    return records, warnings, selection


def _stream_samples(
    text: str, *, stream: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if ERROR_LINE_RE.search(stripped):
            errors.append({
                "code": f"{stream}_error_sample",
                "message": stripped[:500],
                "details": {"stream": stream},
            })
        elif WARNING_LINE_RE.search(stripped):
            warnings.append({
                "code": f"{stream}_warning_sample",
                "message": stripped[:500],
                "details": {"stream": stream},
            })
    return warnings, errors


def _stderr_samples(stderr: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Backward-compatible wrapper for callers that only inspect stderr."""
    return _stream_samples(stderr, stream="stderr")


def build_manifest(
    *,
    input_path: Path,
    output_root: Path,
    execution: dict[str, Any],
    returncode: int,
    stderr: str = "",
    stdout: str = "",
    input_sha256: Optional[str] = None,
    input_size_bytes: Optional[int] = None,
) -> dict[str, Any]:
    """Build a manifest from an existing raw output directory."""
    input_path = input_path.expanduser().resolve()
    output_root = output_root.expanduser().resolve()
    if not input_path.is_file():
        raise FileNotFoundError(f"input not found: {input_path}")
    if not output_root.is_dir():
        raise NotADirectoryError(f"output root not found: {output_root}")

    markdown_records, warnings, selection = _markdown_candidates(output_root, input_path.stem)
    relations, missing_refs, outside_refs, invalid_refs = _collect_asset_relations(
        output_root, markdown_records
    )
    errors: list[dict[str, Any]] = []
    for item in missing_refs:
        errors.append({
            "code": "asset_reference_missing",
            "message": f"Markdown references a missing local asset: {item['reference']}",
            "details": item,
        })
    for item in outside_refs:
        errors.append({
            "code": "asset_reference_outside_root",
            "message": f"Markdown references a local asset outside the output root: {item['reference']}",
            "details": item,
        })
    for item in invalid_refs:
        errors.append({
            "code": "asset_reference_invalid",
            "message": f"Markdown references an invalid local asset: {item['reference']}",
            "details": item,
        })

    assets: list[dict[str, Any]] = []
    asset_paths: set[str] = set()
    unreferenced_assets: list[str] = []
    for path in _iter_files(output_root):
        rel = _safe_relative(output_root, path)
        is_image = path.suffix.lower() in IMAGE_SUFFIXES
        if not is_image and rel not in relations:
            continue
        record = _file_record(output_root, path)
        referenced_by = sorted(relations.get(record["path"], set()))
        record["referenced"] = bool(referenced_by)
        record["referenced_by"] = referenced_by
        record["media_type"] = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        assets.append(record)
        asset_paths.add(record["path"])
        if is_image and not referenced_by:
            unreferenced_assets.append(record["path"])
    if unreferenced_assets:
        warnings.append({
            "code": "unreferenced_assets_present",
            "message": f"{len(unreferenced_assets)} output images are not referenced by Markdown",
            "details": {"count": len(unreferenced_assets), "paths": unreferenced_assets},
        })

    stdout_warnings, stdout_errors = _stream_samples(stdout, stream="stdout")
    stderr_warnings, stderr_errors = _stream_samples(stderr, stream="stderr")
    warnings.extend(stdout_warnings)
    warnings.extend(stderr_warnings)
    if returncode != 0:
        errors.extend(stdout_errors)
        errors.extend(stderr_errors)
        errors.append({
            "code": "mineru_returncode_nonzero",
            "message": f"mineru exited with code {returncode}",
            "details": {"returncode": returncode},
        })
    else:
        # A successful parse may still log recoverable backend/driver errors.
        # Keep them visible without invalidating complete artifacts.
        warnings.extend(stdout_errors)
        warnings.extend(stderr_errors)
    if not markdown_records:
        errors.append({
            "code": "no_non_empty_markdown",
            "message": "MinerU produced no non-empty Markdown artifact",
            "details": {},
        })
    if markdown_records and selection["primary_path"] is None:
        errors.append({
            "code": "ambiguous_primary_markdown",
            "message": "multiple Markdown candidates remained after deterministic selection",
            "details": {"candidates": [item["path"] for item in markdown_records]},
        })

    tracked_names = {MANIFEST_FILENAME, OUTPUT_TREE_FILENAME}
    other_files: list[dict[str, Any]] = []
    for path in _iter_files(output_root):
        rel = _safe_relative(output_root, path)
        if (
            rel in asset_paths
            or path.suffix.lower() == ".md"
            or path.name in tracked_names
        ):
            continue
        other_files.append({"path": rel, "size_bytes": path.stat().st_size})

    def _dedupe_diagnostics(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen: set[tuple[str, str]] = set()
        unique: list[dict[str, Any]] = []
        for item in items:
            key = (str(item.get("code", "")), str(item.get("message", "")))
            if key in seen:
                continue
            seen.add(key)
            unique.append(item)
        return unique

    warnings = _dedupe_diagnostics(warnings)
    errors = _dedupe_diagnostics(errors)
    if input_sha256 is None:
        input_sha256 = sha256_file(input_path)
    if not re.fullmatch(r"[0-9a-f]{64}", input_sha256):
        raise ValueError("input_sha256 must be a lowercase SHA256 hex digest")
    if input_size_bytes is None:
        input_size_bytes = input_path.stat().st_size
    if input_size_bytes < 0:
        raise ValueError("input_size_bytes must be non-negative")
    status = "ok" if not errors else ("partial" if markdown_records else "failed")
    return {
        "manifest_schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": utc_now_iso(),
        "generator": {
            "name": "mineru-local",
            "version": TOOL_VERSION,
        },
        "execution": execution,
        "input": {
            "name": input_path.name,
            "suffix": input_path.suffix.lower(),
            "size_bytes": input_size_bytes,
            "sha256": input_sha256,
        },
        "status": status,
        "markdown": {
            "primary": next((item for item in markdown_records if item["is_primary"]), None),
            "candidates": markdown_records,
            "selection": selection,
        },
        "assets": assets,
        "other_files": other_files,
        "warnings": warnings,
        "errors": errors,
    }


def write_manifest(manifest: dict[str, Any], output_root: Path) -> Path:
    """Write output tree + manifest atomically under output_root."""
    output_root = output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    tree_path = output_root / OUTPUT_TREE_FILENAME
    manifest["output_tree"] = build_output_tree(output_root, output_path=tree_path)
    manifest_path = output_root / MANIFEST_FILENAME
    fd, temp_name = tempfile.mkstemp(prefix=".manifest.", suffix=".tmp", dir=str(output_root))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2, sort_keys=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, manifest_path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return manifest_path


def summarize_stderr(stderr: str, *, max_lines: int = 30) -> str:
    return "\n".join((stderr or "").splitlines()[-max_lines:])
