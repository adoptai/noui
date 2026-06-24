# noui-harness — publishable Agent-Harness skill

This folder is the **publishable artifact** that lets an Agent-Harness turn run the
NoUI toolkit *inside the sandbox* to author new skills (capture → compile). It is
distinct from `../noui/`, which is the NoUI toolkit **source**.

Contents:

- `SKILL.md` — the harness-facing skill (frontmatter `name: noui`). Teaches the agent
  to stage + install the toolkit and drive capture/compile. Auth is handled by the
  harness control-plane broker (no Tabby credentials in the sandbox).
- `noui-bundle.zip` — the NoUI toolkit, zipped (root = bundle root, so the agent can
  `unzip` then `pip install -e .`). Built from `../noui/` (excludes `.venv/`,
  `workbench/`, caches, `.env`). Regenerate with `tmp/make_bundle_zip.py`.

## Upload to the org skill store

Publish as the org-scoped `noui` skill (admin action), e.g. via the harness skill
upload route (`POST /v1/end-user/agent-harness/skills`) or `skills_upload.upload_skill`:

- `skill_name`: `noui`
- `skill_md`: `SKILL.md`
- aux files: `noui-bundle.zip` (binary)

S3 layout it lands at: `skills/source=org/org_id=<org>/skill_name=noui/{SKILL.md,noui-bundle.zip}`.

## Runtime prerequisites (harness side)

The sandbox must be seeded with the broker env (Phase 3 — `feat/noui-control-plane-broker`):
`TABBY_API_URL=<broker>`, `NOUI_TABBY_AUTH_MODE=broker`, `NOUI_BROKER_TOKEN=<capability>`,
which requires the worker config `AGENT_HARNESS_NOUI_BROKER_SANDBOX_URL` set and the
broker process (`python -m src.workflows.agent_harness.noui_broker`) running.
