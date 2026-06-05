"""Global custom-vocabulary glossary CRUD.

GET returns the list; PUT replaces it wholesale (the editor sends the full
list). Entries: ``{id?, term, translation?, note?}`` — entries without a term
are dropped.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from ..core.store import Store
from ..deps import get_store

router = APIRouter()


@router.get("/glossary")
async def get_glossary(store: Store = Depends(get_store)) -> dict[str, Any]:
    return {"glossary": await store.list_glossary()}


@router.put("/glossary")
async def put_glossary(request: Request, store: Store = Depends(get_store)) -> dict[str, Any]:
    body = await request.json()
    entries = body.get("glossary") if isinstance(body, dict) else body
    if not isinstance(entries, list):
        raise HTTPException(status_code=400, detail="expected a list of glossary entries")
    clean = [
        e for e in entries
        if isinstance(e, dict) and str(e.get("term") or "").strip()
    ]
    await store.replace_glossary(clean)
    return {"glossary": await store.list_glossary()}
