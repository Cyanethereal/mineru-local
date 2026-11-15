"""Portable configuration and precedence rules for mineru-local.

Configuration lives outside the Skill payload. The resolution order is:

    explicit CLI value > environment value > persisted config > explicit failure

Only machine-specific executable paths and WSL selection are persisted. No
user home path is embedded in the source tree.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional

CONFIG_SCHEMA_VERSION = 1
CONFIG_PATH_ENV = "MINERU_LOCAL_CONFIG"
ALLOWED_ENVIRONMENTS = ("auto", "windows", "wsl")
ALLOWED_IO_POLICIES = ("auto", "native", "staged")
RESOLVED_IO_MODES = ("native", "staged")

ENV_KEYS = {
    "windows_executable": "MINERU_WINDOWS_EXECUTABLE",
    "wsl_executable": "MINERU_WSL_EXECUTABLE",
    "wsl_distro": "MINERU_WSL_DISTRO",
    "default_env": "MINERU_DEFAULT_ENV",
    "io_policy": "MINERU_IO_POLICY",
}

CONFIG_KEYS = (
    "windows_executable",
    "wsl_executable",
    "wsl_distro",
    "default_env",
    "io_policy",
)


class ConfigError(ValueError):
    """Raised when persisted or environment configuration is invalid."""


@dataclass(frozen=True)
class RuntimeSettings:
    windows_executable: Optional[str]
    wsl_executable: Optional[str]
    wsl_distro: Optional[str]
    default_env: str
    io_policy: str
    config_path: Path
    sources: dict[str, str] = field(default_factory=dict)

    def to_dict(self, *, include_sources: bool = True) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "windows_executable": self.windows_executable,
            "wsl_executable": self.wsl_executable,
            "wsl_distro": self.wsl_distro,
            "default_env": self.default_env,
            "io_policy": self.io_policy,
            "config_path": str(self.config_path),
        }
        if include_sources:
            payload["sources"] = dict(self.sources)
        return payload


def default_config_path(
    *,
    environ: Optional[Mapping[str, str]] = None,
    platform: Optional[str] = None,
) -> Path:
    """Return the platform config path, honoring MINERU_LOCAL_CONFIG."""
    env = os.environ if environ is None else environ
    override = env.get(CONFIG_PATH_ENV, "").strip()
    if override:
        return Path(override).expanduser().resolve()

    platform = platform or sys.platform
    if platform.startswith("win"):
        appdata = env.get("APPDATA", "").strip()
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        return base / "mineru-local" / "config.json"
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "mineru-local" / "config.json"
    xdg = env.get("XDG_CONFIG_HOME", "").strip()
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    return base / "mineru-local" / "config.json"


def _validate_config(config: dict[str, Any], path: Path) -> None:
    schema_version = config.get("schema_version", CONFIG_SCHEMA_VERSION)
    if schema_version != CONFIG_SCHEMA_VERSION:
        raise ConfigError(
            f"unsupported mineru-local config schema {schema_version!r} in {path}; "
            f"expected {CONFIG_SCHEMA_VERSION}"
        )
    for key in CONFIG_KEYS:
        value = config.get(key)
        if value is not None and not isinstance(value, str):
            raise ConfigError(f"config field {key!r} must be a string or null in {path}")
    default_env = config.get("default_env")
    if default_env is not None and default_env not in ALLOWED_ENVIRONMENTS:
        raise ConfigError(
            f"config field 'default_env' must be one of {ALLOWED_ENVIRONMENTS}; "
            f"got {default_env!r} in {path}"
        )
    io_policy = config.get("io_policy")
    if io_policy is not None and io_policy not in ALLOWED_IO_POLICIES:
        raise ConfigError(
            f"config field 'io_policy' must be one of {ALLOWED_IO_POLICIES}; "
            f"got {io_policy!r} in {path}"
        )


def load_config(path: Optional[Path] = None) -> dict[str, Any]:
    """Load a config object. A missing file yields an empty config."""
    config_path = (Path(path) if path is not None else default_config_path()).expanduser().resolve()
    if not config_path.exists():
        return {}
    try:
        raw = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"cannot read config {config_path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config root must be a JSON object: {config_path}")
    _validate_config(raw, config_path)
    return raw


def save_config(
    updates: Mapping[str, Any],
    *,
    path: Optional[Path] = None,
    merge: bool = True,
) -> Path:
    """Atomically save updates, preserving unrelated known values by default."""
    config_path = (Path(path) if path is not None else default_config_path()).expanduser().resolve()
    existing = load_config(config_path) if merge and config_path.exists() else {}
    merged = dict(existing)
    for key, value in updates.items():
        if key not in CONFIG_KEYS:
            continue
        if value is None:
            continue
        if not isinstance(value, str):
            raise ConfigError(f"config field {key!r} must be a string")
        merged[key] = value
    merged["schema_version"] = CONFIG_SCHEMA_VERSION
    _validate_config(merged, config_path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{config_path.name}.", suffix=".tmp", dir=str(config_path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(merged, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, config_path)
    finally:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
    return config_path


def _present(value: Any) -> bool:
    return value is not None and str(value).strip() != ""


def _pick(
    field_name: str,
    *,
    cli_value: Any,
    env_value: Any,
    config_value: Any,
    env_name: str,
    config_path: Path,
    default: Optional[str] = None,
) -> tuple[Optional[str], str]:
    if _present(cli_value):
        return str(cli_value).strip(), f"cli:--{field_name.replace('_', '-')}"
    if _present(env_value):
        return str(env_value).strip(), f"env:{env_name}"
    if _present(config_value):
        return str(config_value).strip(), f"config:{config_path}#{field_name}"
    return default, "default" if default is not None else "missing"


def resolve_settings(
    args: Optional[argparse.Namespace] = None,
    *,
    environ: Optional[Mapping[str, str]] = None,
    config_path: Optional[Path] = None,
) -> RuntimeSettings:
    """Resolve runtime settings using CLI > environment > config."""
    env = os.environ if environ is None else environ
    explicit_config = getattr(args, "config", None) if args is not None else None
    chosen_path = config_path or (
        Path(explicit_config).expanduser().resolve()
        if _present(explicit_config)
        else default_config_path(environ=env)
    )
    config = load_config(chosen_path)
    sources: dict[str, str] = {}
    values: dict[str, Optional[str]] = {}
    explicit_target = getattr(args, "env", None) if args is not None else None
    if explicit_target not in ("windows", "wsl"):
        explicit_target = None

    for field_name, env_name in ENV_KEYS.items():
        cli_value = getattr(args, field_name, None) if args is not None else None
        if field_name == "default_env" and explicit_target and not _present(cli_value):
            # --env is a direct execution-target request. It must remain usable
            # even when a lower-precedence MINERU_DEFAULT_ENV value is invalid.
            values[field_name] = explicit_target
            sources[field_name] = "cli:--env"
            continue
        value, source = _pick(
            field_name,
            cli_value=cli_value,
            env_value=env.get(env_name),
            config_value=config.get(field_name),
            env_name=env_name,
            config_path=chosen_path,
            default="auto" if field_name in ("default_env", "io_policy") else None,
        )
        values[field_name] = value
        sources[field_name] = source

    default_env = values["default_env"] or "auto"
    io_policy = values["io_policy"] or "auto"
    if default_env not in ALLOWED_ENVIRONMENTS:
        raise ConfigError(
            f"default_env must be one of {ALLOWED_ENVIRONMENTS}; got {default_env!r}"
        )
    if io_policy not in ALLOWED_IO_POLICIES:
        raise ConfigError(
            f"io_policy must be one of {ALLOWED_IO_POLICIES}; got {io_policy!r}"
        )

    return RuntimeSettings(
        windows_executable=values["windows_executable"],
        wsl_executable=values["wsl_executable"],
        wsl_distro=values["wsl_distro"],
        default_env=default_env,
        io_policy=io_policy,
        config_path=chosen_path,
        sources=sources,
    )


def config_updates_from_args(args: argparse.Namespace) -> dict[str, str]:
    """Return only explicitly supplied config fields from a config-set command."""
    updates: dict[str, str] = {}
    for key in CONFIG_KEYS:
        value = getattr(args, key, None)
        if value is not None:
            updates[key] = value
    return updates
