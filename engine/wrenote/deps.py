"""FastAPI dependency providers.

Thin accessors over ``app.state`` so endpoints declare what they need via
``Depends`` instead of reaching into ``request.app.state`` by hand. Keeps the
routers' signatures explicit and lets tests override a single provider. These
stay app-agnostic (no app import), so there is no import cycle.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import Request

from .core.catalogue import ModelCatalogue
from .core.config import Config
from .core.jobs import JobRegistry
from .core.runtimes import RuntimeManager
from .core.store import Store
from .model_manager import ModelManager


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_recordings_dir(request: Request) -> Path:
    """Where per-session WAVs live (``data.recordings_dir``, resolved)."""
    return Path(request.app.state.config.data.recordings_dir)


def get_jobs(request: Request) -> JobRegistry:
    return request.app.state.jobs


def get_models(request: Request) -> ModelManager:
    return request.app.state.models


def get_runtimes(request: Request) -> RuntimeManager:
    return request.app.state.runtimes


def get_catalogue(request: Request) -> ModelCatalogue:
    return request.app.state.catalogue
