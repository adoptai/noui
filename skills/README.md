# NoUI Skills

Skills for the full NoUI workflow — from environment setup to recording, compiling, and running FastMCP servers and agent skills.

NoUI is agent-agnostic: the FastMCP servers it produces work with any MCP-compatible client, and generated skills target Claude Code, Codex, Cline, OpenCode, or the shared `.agents/skills/` convention.

These meta-skills are compatible with any agent that supports the [Skills](https://github.com/vercel-labs/skills) framework — Claude Code, Cursor, Cline, and others.

## Layout

| Path | Kind |
|---|---|
| `noui/` | Meta-skill — Capture → Compile → Activate (Tabby) |
| `noui-harness/` | Meta-skill — Agent Harness authoring edition |
| `travel/` | **Claude Code plugin** — Airbnb, Expedia, Flydubai, Google Flights (Tabby `/execute`) |
| `quickbooks/` | **Claude Code plugin** — bank reconciliation demo (Tabby `/execute`) |

### Demo plugins

```bash
# From a clone of this repo
claude plugin install ./skills/travel
claude plugin install ./skills/quickbooks
```

Each plugin nests skills under `skills/<slug>/` with `operations/*.py` + vendored `noui_runtime/` calling Tabby `POST /execute/fetch`. See each plugin's `README.md` for profile slugs and prerequisites.

## Installation (meta-skills)

Start with the entry-point skill — it's a discovery guide that lists every other skill with per-skill install commands:

```bash
npx skills add https://github.com/adoptai/noui --skill noui
```

Then invoke `/noui` in your agent and follow the install commands it prints.

> **Human users** can run `npx skills add https://github.com/adoptai/noui` (no `--skill` flag) to open an interactive picker. AI agents must use the per-skill `--skill <name>` form — the picker blocks on stdin.

`npx skills add` installs these NoUI meta-skills to `.agents/skills/` in your current project by default. **Generated** skills from the compile pillar are installed via `python scripts/activate_install.py …`.

## How NoUI works

The `noui` meta-skill runs the end-to-end cycle as **three pillars** (full detail in `noui/SKILL.md` and `noui/references/`):

1. **Capture** — record a login, workflow, or **combined** session **server-side inside Tabby** (VNC via `scripts/capture_record.py`, or agent-driven Autopilot via `scripts/capture_autopilot.py`), then drain + compile with `scripts/capture_import.py`. Capture is server-side — there is no Chrome extension. See `references/pillar-1-capture.md`.
2. **Compile** (deterministic — no LLM, no `ANTHROPIC_API_KEY`) — turn a capture bundle into a FastMCP server and/or an installable Skill, and a login into a tenant-wide App Template. Re-compile a saved bundle with `scripts/compile_workflow.py` / `scripts/compile_login.py`. See `references/pillar-2-compile.md`.
3. **Activate** — register the App Template (`scripts/activate_register.py`), verify auth (`scripts/activate_verify.py`), and install the generated skill into any agent (`scripts/activate_install.py`); run tools through Tabby `/execute`. See `references/pillar-3-activate.md`.

The interface is these `scripts/*.py` (run from `skills/noui/`), not a monolithic CLI. Two auth axes come up throughout — `--auth-mode` (the Tabby **token** mode: `agent_token` / `platform_jwt`) vs `--auth-type` (the target **app's** auth model: `session` / `api-key`) — see `references/auth-modes.md` and `references/tabby-setup.md`.

The `noui-harness/` edition authors the same skills for the Adopt Agent Harness (compile with `--execution-mode harness`).
