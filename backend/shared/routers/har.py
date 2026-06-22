"""Shared router for HAR file upload and download."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.config import settings
from backend.database import get_db
from backend.shared.models import HarFile
from backend.shared.schemas import HarFileOut

logger = logging.getLogger(__name__)

router = APIRouter(tags=["har"])


async def _resolve_session_type(session_id: str, db: AsyncSession) -> str | None:
    """Detect whether session_id belongs to a login, workflow, or capture session."""
    from backend.login.models import LoginSession
    from backend.shared.session_resolution import resolve_domain_session
    from backend.workflow.models import WorkflowSession

    r = await db.execute(select(LoginSession).where(LoginSession.id == session_id))
    if r.scalar_one_or_none():
        return "login"
    r = await db.execute(select(WorkflowSession).where(WorkflowSession.id == session_id))
    if r.scalar_one_or_none():
        return "workflow"
    # session_id might be a capture_session_id — resolve via process_id linkage
    resolved = await resolve_domain_session(session_id, db)
    if resolved:
        return resolved[1]  # session_type
    return None


async def persist_capture_har(
    db: AsyncSession, capture_session_id: str, har_bytes: bytes
) -> HarFile:
    """Persist a HAR captured for a capture session so analysis/export can find it.

    Resolves the capture session to its domain (login/workflow) session, writes the
    HAR under ``{data_dir}/har/{session_type}/{store_id}.har``, sets
    ``CaptureSession.har_file_path``, and upserts the ``HarFile`` row keyed by the
    domain session id + type.

    Shared by the extension-compat upload endpoint and the Tabby-mode autopilot
    stop-capture (which receives the HAR server-side from the worker). Keeping both
    paths on this one routine is what guarantees the exporter can locate the HAR
    regardless of how it was captured.
    """
    from backend.elicitation.models import CaptureSession
    from backend.shared.session_resolution import resolve_domain_session

    resolved = await resolve_domain_session(capture_session_id, db)
    if resolved:
        store_id, session_type = resolved
    else:
        session_type = await _resolve_session_type(capture_session_id, db) or "unknown"
        store_id = capture_session_id

    har_dir = Path(settings.data_dir) / "har" / session_type
    har_dir.mkdir(parents=True, exist_ok=True)
    file_path = har_dir / f"{store_id}.har"
    file_path.write_bytes(har_bytes)

    # Keep CaptureSession.har_file_path in sync so the elicitation GET route finds it.
    cs_result = await db.execute(
        select(CaptureSession).where(CaptureSession.id == capture_session_id)
    )
    cs = cs_result.scalar_one_or_none()
    if cs:
        cs.har_file_path = str(file_path)

    existing = await db.execute(
        select(HarFile).where(
            HarFile.session_id == store_id,
            HarFile.session_type == session_type,
        )
    )
    har_record = existing.scalar_one_or_none()
    if har_record:
        har_record.file_path = str(file_path)
    else:
        har_record = HarFile(
            session_id=store_id, session_type=session_type, file_path=str(file_path)
        )
        db.add(har_record)
    await db.commit()
    await db.refresh(har_record)
    return har_record


@router.post("/sessions/{session_id}/har", response_model=HarFileOut, status_code=201)
async def upload_har(
    session_id: str,
    file: UploadFile,
    session_type: str = Query(..., description="login or workflow"),
    db: AsyncSession = Depends(get_db),
) -> HarFile:
    """Upload a HAR file for the given session.

    The file is stored under ``<data_dir>/har/<session_type>/<session_id>.har``.
    Re-uploading for the same session replaces the previous file record.
    """
    har_dir = Path(settings.data_dir) / "har" / session_type
    har_dir.mkdir(parents=True, exist_ok=True)

    file_path = har_dir / f"{session_id}.har"

    # Validate that uploaded content is parseable JSON (HAR is JSON)
    content = await file.read()
    try:
        json.loads(content)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=400, detail=f"Invalid HAR file (not valid JSON): {exc}"
        ) from exc

    file_path.write_bytes(content)
    logger.info("Saved HAR for session %s (%s) to %s", session_id, session_type, file_path)

    # Upsert: remove old record for this session if it exists
    existing = await db.execute(
        select(HarFile).where(
            HarFile.session_id == session_id,
            HarFile.session_type == session_type,
        )
    )
    old = existing.scalar_one_or_none()
    if old:
        await db.delete(old)
        await db.flush()

    har_record = HarFile(
        session_id=session_id,
        session_type=session_type,
        file_path=str(file_path),
    )
    db.add(har_record)
    await db.commit()
    await db.refresh(har_record)
    return har_record


@router.get("/sessions/{session_id}/har")
async def download_har(
    session_id: str,
    session_type: str = Query(..., description="login or workflow"),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Download the HAR file for the given session."""
    result = await db.execute(
        select(HarFile).where(
            HarFile.session_id == session_id,
            HarFile.session_type == session_type,
        )
    )
    har_record = result.scalar_one_or_none()
    if not har_record:
        raise HTTPException(status_code=404, detail="No HAR file found for this session")

    file_path = Path(har_record.file_path)
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="HAR file not found on disk")

    return FileResponse(
        path=str(file_path),
        media_type="application/json",
        filename=f"{session_id}.har",
    )


@router.post("/capture-sessions/{session_id}/har", status_code=201)
async def upload_har_compat(
    session_id: str,
    file: UploadFile,
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Extension-compat endpoint: upload HAR without specifying session_type.

    The extension sends the ``capture_session_id`` in the URL.  This endpoint
    resolves the domain session (login or workflow) via
    ``CaptureSession → process_id → LoginSession / WorkflowSession`` and stores
    the HAR under the **domain** session ID so that analysis endpoints find it
    with a simple ``session_id + session_type`` query.
    """
    content = await file.read()
    try:
        json.loads(content)
    except json.JSONDecodeError:
        # Store even if invalid JSON — don't block the extension upload
        logger.warning("HAR upload for %s is not valid JSON — storing anyway", session_id)

    har_record = await persist_capture_har(db, session_id, content)
    logger.info(
        "Compat HAR upload: capture_session=%s → stored as %s/%s",
        session_id,
        har_record.session_type,
        har_record.session_id,
    )
    return {
        "id": har_record.id,
        "session_id": har_record.session_id,
        "session_type": har_record.session_type,
        "file_path": har_record.file_path,
    }


@router.get("/sessions/{session_id}/har/meta", response_model=HarFileOut)
async def get_har_meta(
    session_id: str,
    session_type: str = Query(..., description="login or workflow"),
    db: AsyncSession = Depends(get_db),
) -> HarFile:
    """Return metadata about the stored HAR file without downloading it."""
    result = await db.execute(
        select(HarFile).where(
            HarFile.session_id == session_id,
            HarFile.session_type == session_type,
        )
    )
    har_record = result.scalar_one_or_none()
    if not har_record:
        raise HTTPException(status_code=404, detail="No HAR file found for this session")
    return har_record
