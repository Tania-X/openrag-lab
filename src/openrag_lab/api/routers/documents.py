"""Knowledge base document endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from openrag_lab.client import OpenRAGClient, OpenRAGError
from openrag_lab.config import get_settings

router = APIRouter(tags=["documents"])


@router.get("/api/documents")
def list_documents() -> dict:
    settings = get_settings()
    try:
        with OpenRAGClient(settings.openrag_base_url, settings.openrag_api_key) as client:
            files = client.list_files()
        return {"total": len(files), "files": files}
    except OpenRAGError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
