"""Layered configuration loader.

Per design.v1.1 §6. Loads layered config from (low → high priority):

1. Hard-coded defaults (Pydantic model defaults)
2. Repo default YAML (backend/config.yaml)
3. User override YAML (~/.wrenote/config.yaml)
4. Environment variables (WRENOTE_<SECTION>__<KEY>__... with `__` nesting)

All string values starting with `~` are expanded via `Path.expanduser()`.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    log_level: str = "info"


class BackendConfig(BaseModel):
    backend: str
    params: dict[str, Any] = Field(default_factory=dict)


class SessionConfig(BaseModel):
    default_src_lang: str = "en"
    default_tgt_lang: str = "zh"


class Config(BaseSettings):
    """Top-level settings. Env vars override init kwargs (= loaded YAML)."""

    model_config = SettingsConfigDict(
        env_prefix="WRENOTE_",
        env_nested_delimiter="__",
        case_sensitive=False,
        extra="ignore",
    )

    server: ServerConfig = Field(default_factory=ServerConfig)
    stt: BackendConfig = Field(default_factory=lambda: BackendConfig(backend="mock"))
    vad: BackendConfig = Field(default_factory=lambda: BackendConfig(backend="disabled"))
    translator: BackendConfig = Field(default_factory=lambda: BackendConfig(backend="mock"))
    speaker: BackendConfig = Field(default_factory=lambda: BackendConfig(backend="ecapa"))
    chat: BackendConfig = Field(default_factory=lambda: BackendConfig(backend="mock"))
    session: SessionConfig = Field(default_factory=SessionConfig)

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Env vars take precedence over the YAML we pass via init kwargs.
        return (env_settings, init_settings, dotenv_settings, file_secret_settings)


# ---------- Loader ----------


def _default_config_path() -> Path:
    """Bundled default config. When frozen (PyInstaller), it lives next to the
    other bundled data under ``sys._MEIPASS``; in dev it's the repo's config.yaml."""
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", ".")) / "config.yaml"
    return Path(__file__).resolve().parent.parent.parent / "config.yaml"


REPO_DEFAULT_CONFIG = _default_config_path()
USER_CONFIG = Path.home() / ".wrenote" / "config.yaml"


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge `override` into `base`. Override wins on conflict."""
    out = dict(base)
    for key, value in override.items():
        if key in out and isinstance(out[key], dict) and isinstance(value, dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def _expand_paths(obj: Any) -> Any:
    """Walk a nested structure; for any string starting with `~`, expanduser it."""
    if isinstance(obj, dict):
        return {k: _expand_paths(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_expand_paths(v) for v in obj]
    if isinstance(obj, str) and obj.startswith("~"):
        return str(Path(obj).expanduser())
    return obj


def load_config(
    yaml_paths: list[Path] | None = None,
    *,
    use_env: bool = True,
) -> Config:
    """Load layered config.

    Args:
        yaml_paths: List of YAML files to load in order (later wins on conflict).
            Defaults to [repo config.yaml, user config.yaml].
        use_env: If False, skip the env-var override layer (handy for tests).
    """
    if yaml_paths is None:
        yaml_paths = [REPO_DEFAULT_CONFIG, USER_CONFIG]

    merged: dict[str, Any] = {}
    for path in yaml_paths:
        if not path.exists():
            continue
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        if not isinstance(data, dict):
            raise ValueError(f"Config file {path} did not parse to a mapping")
        merged = _deep_merge(merged, data)

    merged = _expand_paths(merged)

    if not use_env:
        # Construct via plain Pydantic, bypassing env-var source entirely.
        return Config.model_validate(merged)

    return Config(**merged)


def get_env_overrides_summary() -> dict[str, str]:
    """Return WRENOTE_*-prefixed env vars (for debug/logging)."""
    return {k: v for k, v in os.environ.items() if k.startswith("WRENOTE_")}
