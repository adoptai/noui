"""REST endpoints for autopilot recording runs and capture control.

The heavy lifting (driving the browser) is done by Claude Code via the
/browser-commands/execute endpoint.  This router handles:
  - CRUD for autopilot run records
  - Programmatic capture start/stop (so the extension popup is not needed)
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.autopilot.models import AutopilotRecordingRun
from backend.autopilot.schemas import AutopilotRunCreate, AutopilotRunOut
from backend.database import get_db
from backend.elicitation.browser_bridge import _use_tabby_driver, execute_command

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/autopilot-recordings", tags=["autopilot-recordings"])


async def _get_run(run_id: str, db: AsyncSession) -> AutopilotRecordingRun:
    result = await db.execute(
        select(AutopilotRecordingRun).where(AutopilotRecordingRun.id == run_id)
    )
    run = result.scalar_one_or_none()
    if not run:
        raise HTTPException(status_code=404, detail="Autopilot run not found")
    return run


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.post("", response_model=AutopilotRunOut, status_code=201)
async def create_autopilot_run(
    data: AutopilotRunCreate,
    db: AsyncSession = Depends(get_db),
) -> AutopilotRecordingRun:
    """Create a new autopilot recording run record.

    This only creates the record.  The actual browser driving is done by
    Claude Code through the skill flow.
    """
    run = AutopilotRecordingRun(
        website_url=data.website_url,
        login_url=data.login_url,
        task_description=data.task_description,
        success_condition=data.success_condition,
        stop_condition=data.stop_condition,
        allowed_side_effects_json=json.dumps(data.allowed_side_effects),
        forbidden_side_effects_json=json.dumps(data.forbidden_side_effects),
        test_data_json=json.dumps(data.test_data),
        tabby_profile_id=data.tabby_profile_id,
        mfa_policy=data.mfa_policy,
        max_browser_steps=data.max_browser_steps,
        max_duration_seconds=data.max_duration_seconds,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    logger.info("Created autopilot run %s for %s", run.id, run.website_url)
    return run


@router.get("", response_model=list[AutopilotRunOut])
async def list_autopilot_runs(
    db: AsyncSession = Depends(get_db),
) -> list[AutopilotRecordingRun]:
    result = await db.execute(
        select(AutopilotRecordingRun).order_by(AutopilotRecordingRun.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/{run_id}", response_model=AutopilotRunOut)
async def get_autopilot_run(
    run_id: str,
    db: AsyncSession = Depends(get_db),
) -> AutopilotRecordingRun:
    return await _get_run(run_id, db)


@router.patch("/{run_id}", response_model=AutopilotRunOut)
async def update_autopilot_run(
    run_id: str,
    data: dict,
    db: AsyncSession = Depends(get_db),
) -> AutopilotRecordingRun:
    """Partial update a run (used by CLI to update status, session IDs, etc.)."""
    run = await _get_run(run_id, db)
    allowed_fields = {
        "status",
        "login_url",
        "success_condition",
        "stop_condition",
        "tabby_profile_id",
        "login_session_id",
        "workflow_session_id",
        "capture_session_id",
        "server_id",
        "mcp_output_path",
        "agent_trace_path",
        "tools_count",
        "failure_reason",
    }
    for key, value in data.items():
        if key in allowed_fields:
            setattr(run, key, value)
    run.updated_at = datetime.now(UTC)
    if data.get("status") == "completed":
        run.completed_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Capture control — lets the CLI start/stop capture without the popup
# ---------------------------------------------------------------------------


class CaptureControlIn(BaseModel):
    capture_session_id: str
    project_id: str = ""
    process_id: str = ""


@router.post("/start-capture")
async def start_capture(data: CaptureControlIn):
    """Start HAR + click + URL capture.

    In Tabby mode: sends har_start to the worker via execute/browser.
    In extension mode: verifies extension liveness and injects click tracker.
    """
    if _use_tabby_driver():
        result = await execute_command("har_start", {})
        return {
            "status": "started",
            "capture_session_id": data.capture_session_id,
            "driver": "tabby",
            "note": "HAR capture started on the Tabby worker via execute/browser.",
            "har_status": result.get("data", {}),
        }

    # Extension mode (original behavior)
    try:
        page_info = await execute_command("get_page_info", {})
        tab_id = page_info.get("tabId")
    except TimeoutError:
        tab_id = None

    try:
        await execute_command(
            "eval_js",
            {"code": "document.title"},
        )
    except TimeoutError as exc:
        raise HTTPException(
            504,
            "Extension not responding. Is Chrome running with the NoUI extension?",
        ) from exc

    if tab_id:
        try:
            await execute_command(
                "eval_js",
                {
                    "code": (
                        "if (!window.__adoptClickTracker) {"
                        "  let s = document.createElement('script');"
                        "  s.src = chrome.runtime.getURL('content/click-tracker.js');"
                        "  document.head.appendChild(s);"
                        "}"
                        "return {injected: true};"
                    )
                },
            )
        except Exception:
            pass

    return {
        "status": "started",
        "capture_session_id": data.capture_session_id,
        "tab_id": tab_id,
        "driver": "extension",
        "note": "HAR capture is managed by the extension capture session. Use the extension or call PUT /capture-sessions/{id}/start first.",
    }


@router.post("/stop-capture")
async def stop_capture(data: CaptureControlIn, db: AsyncSession = Depends(get_db)):
    """Stop capture and retrieve HAR.

    In Tabby mode: sends har_stop to the worker, receives HAR JSON, and
    stores it via the shared HAR storage path so the exporter can find it.
    In extension mode: the extension uploads HAR when the capture session is stopped.
    """
    if _use_tabby_driver():
        result = await execute_command("har_stop", {})
        result_data = result.get("data", {})
        har_json = result_data.get("har")

        # Persist the worker-captured HAR exactly like the extension upload path:
        # keyed by the domain (workflow) session id + type, with a HarFile row and
        # CaptureSession.har_file_path set. Without this the exporter can't locate it.
        stored_path = None
        if har_json:
            from backend.shared.routers.har import persist_capture_har

            har_record = await persist_capture_har(
                db, data.capture_session_id, json.dumps(har_json).encode()
            )
            stored_path = har_record.file_path
            logger.info(
                "Tabby HAR stored for capture %s → %s (%d entries)",
                data.capture_session_id,
                stored_path,
                result_data.get("entry_count", 0),
            )

        return {
            "status": "stopped",
            "capture_session_id": data.capture_session_id,
            "driver": "tabby",
            "entry_count": result_data.get("entry_count", 0),
            "har_file_path": stored_path,
            "note": "HAR captured server-side by the Tabby worker.",
        }

    return {
        "status": "stopped",
        "capture_session_id": data.capture_session_id,
        "driver": "extension",
        "note": "Call PUT /capture-sessions/{id}/stop to finalize. The extension uploads HAR on stop.",
    }
