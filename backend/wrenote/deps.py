"""FastAPI dependency providers.

Thin accessors over ``app.state`` so endpoints declare what they need via
``Depends`` instead of reaching into ``request.app.state`` by hand. Keeps the
routers' signatures explicit and lets tests override a single provider. These
stay app-agnostic (no app import), so there is no import cycle.
"""
from __future__ import annotations

from fastapi import Request

from .core.config import Config
from .core.jobs import JobRegistry
from .core.store import Store
from .model_manager import ModelManager


def get_store(request: Request) -> Store:
    return request.app.state.store


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_jobs(request: Request) -> JobRegistry:
    return request.app.state.jobs


def get_models(request: Request) -> ModelManager:
    return request.app.state.models
