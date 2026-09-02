"""The committed API contract must match the code.

``contract/openapi.json`` is what clients are built against. If this fails,
the route surface or a request/response model changed: regenerate with
``python -m wrenote.contract`` and review the diff as a contract change.
"""
from __future__ import annotations

import json

from wrenote import contract


def test_openapi_contract_is_current():
    assert contract.OPENAPI_PATH.exists(), "run `python -m wrenote.contract` to create it"
    committed = contract.OPENAPI_PATH.read_text(encoding="utf-8")
    assert committed == contract.render(contract.build_openapi()), (
        "contract/openapi.json is stale — run `python -m wrenote.contract` and commit the diff"
    )


def test_contract_is_versioned_under_v1():
    schema = json.loads(contract.OPENAPI_PATH.read_text(encoding="utf-8"))
    paths = set(schema["paths"])
    assert "/health" in paths  # the shell's readiness probe stays unversioned
    versioned = paths - {"/health"}
    assert versioned and all(p.startswith("/v1/") for p in versioned), sorted(versioned)
    assert "/v1/info" in paths and "/v1/compute/status" in paths
