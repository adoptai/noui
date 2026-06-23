---
name: noui-autopilot
description: Use this skill when the user wants to automatically record a browser workflow and generate an MCP server without manually using the Chrome extension popup. Triggers on "autopilot record", "auto-record a workflow", "automatically capture a workflow", "noui autopilot", "record this website automatically", "generate an MCP from this site", or "I want to automate this website without manual recording". This skill makes YOU (Claude Code) the browser agent — you drive the browser directly.
---

# NoUI Autopilot Recording

You ARE the browser automation agent. You will drive a real browser to record a workflow, then compile it into a FastMCP server. You control the browser directly via HTTP commands.

All commands run from the `noui/` directory using `.venv/bin/python cli/main.py`.

**Execution mode:** autopilot-exported servers inherit the execute-fetch default — generated operations run inside Tabby's browser session via `noui_runtime.execute`. Pass `--execution-mode http` to `noui autopilot export` if you need the legacy `httpx + resolve_auth` path. See `/noui-record-workflow` → *How Execution Works*.

### Browser driver modes

The autopilot has two browser driver modes, selected automatically by environment:

| Mode | When | Prerequisites |
|------|------|---------------|
| **Tabby** (headless, no extension) | `TABBY_API_URL` + `TABBY_CLIENT_ID` + `TABBY_PROFILE_ID` are set | See the full Tabby-mode checklist below. No Chrome extension needed. |
| **Extension** (local Chrome) | Tabby env vars not set | Chrome with the NoUI extension loaded and connected to `localhost:8002`. `/noui-setup` must be complete. |

In Tabby mode, browser commands go to Tabby's `POST /execute/browser` endpoint — the worker drives Playwright directly. HAR capture is server-side (`har_start`/`har_stop`), no extension capture session needed. This enables headless/server-side autopilot for agent-builder pipelines.

In extension mode (the original path), commands go through the Chrome extension's in-memory queue. HAR capture is extension-managed. The "Verify Extension" preflight step (Step 1.5) applies only to this mode.

#### Tabby-mode prerequisites (read before relying on `TABBY_*`)

The driver auto-selects Tabby mode when `TABBY_API_URL` + `TABBY_CLIENT_ID` + `TABBY_PROFILE_ID` are set, but that gate is **not** the full requirement set:

- **`TABBY_CLIENT_SECRET` is also required.** The mode gate doesn't check it, but the first browser command mints an agent token and reads `TABBY_CLIENT_SECRET` — if it's missing you get a hard failure, not a graceful fallback. Set all four.
- **The profile's app must have `execute_enabled = true`.** `/execute/browser` (and `/execute/fetch`) are gated on it; it defaults to `false`. `noui tabby setup` / `session ensure` set it on the apps they create, but a pre-existing or externally-created app may need re-provisioning. See `/noui-tabby-integration`.
- **A live HEALTHY session must exist for the profile.** Tabby resolves `TABBY_PROFILE_ID` → a HEALTHY session → the worker. No HEALTHY session ⇒ `/execute/browser` fails. Bring one up with `noui tabby session ensure --profile <slug>`.
- **`HEALTHY` ≠ authenticated.** A session can reach HEALTHY before login completes (the keepalive `url_check` may match a pre-login page). The recorded workflow will then silently capture unauthenticated 401/403 bodies wrapped as 200s. Pre-warm at the authenticated entry point and verify one known-authenticated request returns real data before trusting the capture. See `/noui-tabby-integration`.
- **Egress is allowlisted.** The Tabby worker browser can only reach domains in the session's egress allowlist (the app's `target_urls` + the default allowlist). Driving to an off-allowlist domain fails — add the target's domain(s) to the app's `target_urls` before recording.
- **`take_screenshot` is not available in Tabby CDP-streaming mode** — it returns HTTP 500. Use `get_page_summary` / `query_elements` (and `cdp_get_accessibility_tree`) to read the page instead.

For how to stand up a local Tabby session for this, see `/noui-tabby-integration` (and note that `noui tabby start` alone only launches the compose API — a live session needs the Kind full stack or a cloud Tabby).

#### Tabby-mode quickstart

Once a HEALTHY, `execute_enabled` session exists for the profile:

```bash
# 1. Point at the live Tabby and the profile (all four are required)
export TABBY_API_URL=http://localhost:18080   # :8080 compose / :18080 Kind / hosted URL
export TABBY_CLIENT_ID=agent_cl_...
export TABBY_CLIENT_SECRET=secret_sk_...
export TABBY_PROFILE_ID=<profile_slug>

# 2. Confirm a live session for the profile (Kind/cloud; not compose-only)
.venv/bin/python cli/main.py tabby session ensure --profile <profile_slug>

# 3. Start the NoUI backend (it reads the TABBY_* env at startup)
.venv/bin/python cli/main.py start
```

Then run the normal capture flow below — **Steps 2–8, skipping Step 1.5** (`verify-extension` is a no-op in Tabby mode and reports "Tabby mode active — no Chrome extension to verify"). HAR is captured server-side and, on `stop-capture`, persisted where `export` reads it — no manual HAR upload needed.

---

## Critical Rules (Never Violate)

- **YOU are the browser agent.** Do not call any external AI API. You read page state, decide what to do, and execute browser commands yourself.
- **ALWAYS** start the backend before doing anything else.
- **NEVER** type raw passwords into chat output or logs. When you type credentials into the browser, use the browser command — the value goes to the extension, not to the conversation.
- **ALWAYS** call `get_page_summary` before clicking or typing to understand what elements are available.
- **NEVER** click Send, Submit, Pay, Delete, Publish, or Invite buttons unless the user explicitly allowed that side effect.
- **ALWAYS** stop capture before exporting MCP.
- **NEVER** skip capture validation — if no API calls were recorded, do not export an empty MCP.
- If you encounter MFA, captcha, or any challenge you cannot handle, **STOP and ask the user** to complete it manually in the browser, then resume.

---

## Step 0 — Gather Input

Ask the user for:
1. **Website URL** (required)
2. **Username and password** (if the site requires login)
3. **What they want to do** — the task description (required)
4. **Any side effects to allow or forbid** (optional)

Example:
> User: "Autopilot record on https://app.example.com — log in as admin@example.com / mypass123, then create a draft invoice for Acme Corp for $500"

---

## Step 1 — Start the Backend

```bash
.venv/bin/python cli/main.py start
```

Verify:

```bash
.venv/bin/python cli/main.py status
```

---

## Step 1.5 — Preflight: Verify Extension

Before starting capture, verify the extension is loaded and supports all commands:

```bash
.venv/bin/python cli/main.py autopilot verify-extension
```

This tests `get_page_info`, `get_page_summary`, `press_key`, and `query_elements`. If any command fails with "UNSUPPORTED", ask the user to reload the extension in `chrome://extensions` and re-run.

**Do NOT skip this step.** Starting capture with a stale extension will silently fail — HAR recording won't work, and you won't know until `stop-capture`, wasting the entire session.

> **⚠ Extension reload warning:** If the Chrome extension is reloaded at any point during an active capture session, the capture is effectively dead — the extension loses its in-memory HAR buffer and capture state. If this happens: stop the broken capture with `autopilot stop-capture`, then restart with `autopilot resume-capture <wf_id>` to create a fresh capture session on the same workflow.

---

## Step 2 — Start Capture

```bash
.venv/bin/python cli/main.py autopilot start-capture "<TaskName>" "<website_url>"
```

This single command creates the workflow session, capture session, starts HAR capture, click tracking, and URL monitoring in the extension. Note the **workflow_session_id** and **capture_session_id** from the output — you need both for stop and export.

---

## Step 3 — Navigate to the Website

```bash
.venv/bin/python cli/main.py autopilot browser navigate url=<website_url>
```

Wait 2 seconds for the page to load, then read the page:

```bash
.venv/bin/python cli/main.py autopilot browser get_page_summary
```

---

## Step 4 — Login (if credentials provided)

Read the page to find the login form:

```bash
.venv/bin/python cli/main.py autopilot browser get_page_summary
```

Then type credentials and submit. Example flow:

```bash
# Type username
.venv/bin/python cli/main.py autopilot browser type_into_label label=Username text=admin@example.com

# Type password
.venv/bin/python cli/main.py autopilot browser type_into_label label=Password text=secret123

# Click sign in
.venv/bin/python cli/main.py autopilot browser click_by_text "Sign In"
```

Wait for the page to settle, then verify login succeeded:

```bash
sleep 2
.venv/bin/python cli/main.py autopilot browser get_page_summary
```

If you see MFA/captcha, tell the user:
> "I see an MFA/captcha challenge. Please complete it in the browser, then tell me when you're done."

---

## Step 5 — Perform the Workflow Task

This is the core loop. You read the page, decide what to do, and execute:

1. **Read page state:**
   ```bash
   .venv/bin/python cli/main.py autopilot browser get_page_summary
   ```

2. **Decide** what action to take based on the task description and visible elements.

3. **Execute** the action:
   ```bash
   # Click a button or link
   .venv/bin/python cli/main.py autopilot browser click_by_text "New Invoice"

   # Click by CSS selector
   .venv/bin/python cli/main.py autopilot browser click_element selector=#create-btn

   # Type into a field
   .venv/bin/python cli/main.py autopilot browser type_into_label label="Customer" text="Acme Corp"

   # Select a dropdown option
   .venv/bin/python cli/main.py autopilot browser select_option selector=#status value=draft

   # Press Enter
   .venv/bin/python cli/main.py autopilot browser press_key Enter

   # Wait for an element to appear
   .venv/bin/python cli/main.py autopilot browser wait_for_selector selector=.success-message

   # Navigate to a URL
   .venv/bin/python cli/main.py autopilot browser navigate url=https://app.example.com/invoices/new
   ```

4. **Repeat** until the task is complete.

### Safety Checks

Before clicking any button that could have side effects, check if it's in the forbidden list:
- **STOP before** Send, Submit, Pay, Delete, Publish, Invite — unless explicitly allowed
- **ASK the user** if uncertain about a destructive action

---

## Handling Complex UIs (SPAs, Custom Widgets)

### Timing: Wait for Dynamic Content

SPAs load content asynchronously. After any navigation or click that triggers a page transition:

1. Use `wait_for_selector` to wait for a specific element that signals the page is ready, OR
2. Use `wait_for_url` if the URL changes, OR
3. Wait 2-3 seconds and re-run `get_page_summary` to check if elements have appeared.

Do NOT blindly proceed after a click — always verify the page state updated before your next action.

### Autocomplete / Typeahead Fields

These fields show a dropdown of suggestions as you type. **Do NOT use `click_by_text` on autocomplete suggestions** — most sites render them as custom widgets invisible to element queries, so `click_by_text` will fail. Use keyboard selection instead.

**Primary strategy (use this first):**
1. `type_into_label label="<field>" text="<search text>"` — type the search term
2. Wait 2 seconds for the suggestions dropdown to appear
3. `press_key ArrowDown` to highlight the first (or desired) suggestion
4. `press_key Enter` to select it
5. `get_page_summary` — verify the field now shows the selected value

**Fallback (only if keyboard selection doesn't work):**
1. After typing, run `get_page_summary` to look for dropdown/listbox elements
2. Try `click_by_text "<suggestion text>"` if visible
3. If suggestions are not in `get_page_summary` (Shadow DOM, canvas-based UIs), use `take_screenshot` to see the dropdown, then `query_elements selector="[role='option'], [role='listbox'] li, .autocomplete-item"` to find selectable items

### Date Pickers

Date pickers are rarely standard `<input type="date">`. They are usually custom widgets.

Strategy:
1. Click the date field to open the picker
2. `get_page_summary` or `query_elements` to find the picker's navigation (month/year arrows, day cells)
3. Navigate month-by-month using the arrow buttons if needed
4. Click the target date using one of these approaches (in priority order):
   - `click_element` with the unique selector returned by `query_elements` — selectors are now unique and directly usable
   - `click_at x=<center_x> y=<center_y>` using the `rect` from `query_elements` (compute center: `x + width/2`, `y + height/2`) — best for grids where cells lack unique attributes
   - `click_by_text "<day number>"` — only works if day cells are buttons/links
   - `click_element selector="[data-date='2025-01-15']"` — if the picker uses data attributes
5. If the picker has separate fields (month dropdown, day dropdown, year dropdown), treat each as its own interaction
6. When closing the picker, use `click_by_text "Done" exact=true` to avoid matching skip-navigation links

**Important:** If clicking a calendar cell (`td`) has no effect, click the **inner interactive element** instead (e.g., `div.uitk-day-button`, `span`, `[role='gridcell']`). The container element may not handle click events — the actual click target is often a child element.

Fallback: Try `type_text` directly into the date input with the expected format (e.g., `01/15/2025`, `2025-01-15`) — some pickers accept typed input even if they show a widget.

### Custom Dropdowns (non-`<select>` elements)

Many SPAs replace `<select>` with divs styled as dropdowns. `select_option` will NOT work on these.

Strategy:
1. Click the dropdown trigger element to open it
2. `get_page_summary` — look for the options that appeared (often `[role="option"]`, `[role="listbox"]`, or `li` elements)
3. `click_by_text "<option text>"` to select
4. If options are not visible in `get_page_summary`, use `query_elements selector="[role='option'], [role='menuitem'], ul.dropdown li"`

### When You Are Stuck: Use Screenshots

If `get_page_summary` returns elements but you cannot figure out what the page looks like or which element to interact with:

```bash
.venv/bin/python cli/main.py autopilot browser take_screenshot
```

This captures the current browser viewport. The response includes a `file_path` — use it to read the screenshot image:

```bash
# The response will include: "file_path": "/path/to/data/screenshots/<uuid>.png"
# Use that path to view the screenshot
```

Use it to:
- See what a custom widget actually looks like
- Verify whether a dropdown/modal is open or closed
- Check if an error message appeared
- Understand spatial layout that element lists cannot convey

Use sparingly — screenshots are heavier than text queries.

---

## Retry and Fallback Strategy

When an action fails, follow this escalation sequence:

### `click_by_text` matches wrong element

1. Use `exact=true` for an exact text match: `click_by_text "Done" exact=true`
2. Note: elements smaller than 10x10px (e.g., skip-navigation links) are automatically filtered out

### `click_by_text` fails ("not found")

1. Run `get_page_summary` to see the exact text of all visible elements
2. Try with a shorter or different substring (`click_by_text` uses case-insensitive partial match)
3. Try `click_element` with a CSS selector from the summary — selectors from `query_elements` and `get_page_summary` are unique and can be used directly
4. Try `click_at` with coordinates from `query_elements` rect output
5. If still failing, the element may be in shadow DOM or an iframe — use `query_elements` with broader selectors or `take_screenshot`

### `type_into_label` fails ("not found")

1. The field may lack a label/placeholder. Run `get_page_summary` and find the input by its index
2. Use `type_text selector="<css-selector>"` with the selector from the summary
3. If the field is a contenteditable div (not an input), `click_element` on it first, then try `type_text`

### Element exists but click has no effect

1. Try clicking the **inner interactive element** instead — container elements (e.g., `td`, `div`) may not handle click events; the actual target is often a child (e.g., `div.day-button`, `span`, `[role='gridcell']`)
2. Try `click_at` with the element's center coordinates from `query_elements` rect — this bypasses selector issues entirely
3. The element might need a different event — try `eval_js code="document.querySelector('<selector>').click()"`
4. The element might be behind an overlay — check for modals with `query_elements selector="[role='dialog'], .modal, .overlay"`
5. Take a screenshot to see what is blocking interaction

### Page seems stuck / not updating after action

1. Wait 2-3 seconds and re-check with `get_page_summary`
2. Check if the URL changed with `get_page_info`
3. Take a screenshot to see the current state
4. The action may have triggered a loading spinner — use `wait_for_selector` for the expected next element

### `eval_js` blocked by CSP

This is common on Google, Facebook, and other major sites. Fall back to `click_element`, `type_text`, and `press_key` — these use the extension's content script which is not blocked by CSP.

---

## Step 6 — Stop Capture

```bash
.venv/bin/python cli/main.py autopilot stop-capture <workflow_session_id> <capture_session_id>
```

This stops the extension capture (triggering HAR upload), stops the capture session in the database, completes the workflow session, and waits for the HAR upload. It reports whether a HAR file was successfully captured.

If no HAR was captured, tell the user and suggest re-recording manually with `/noui-record-workflow`.

---

## Step 7 — Export MCP

```bash
.venv/bin/python cli/main.py autopilot export <workflow_session_id> <capture_session_id>
```

If the user has a Tabby profile, add `--profile-slug <slug>`.

Note the `server_id` from the output.

If 0 tools were generated, the workflow may only have recorded HTML pages without API calls — the site may use server-side rendering. Tell the user.

---

## Step 8 — Install and Report

```bash
.venv/bin/python cli/main.py mcp install <server_id> claude-code
```

Report to the user:
- Server ID
- Number of tools generated
- Output path
- Install command (already run)

---

## Available Browser Commands

All browser commands are executed via:
```bash
.venv/bin/python cli/main.py autopilot browser <command> [key=value ...]
```

They proxy to `POST http://localhost:8002/browser-commands/execute` with body `{"command_type": "<command>", "params": {...}}`. The extension picks up the command and returns a result.

---

#### `get_page_info`
**Params:** none
**Response:** `{ url: string, title: string, favIconUrl: string, tabId: number }`

---

#### `get_page_summary`
**Params:** none
**Response:** `{ url: string, title: string, total: number, returned: number (max 60), elements: [{ index, tagName, type, id, name, text (max 100 chars), placeholder, ariaLabel, href, disabled, selector }] }`
Returns all visible interactive elements (`a, button, input, select, textarea, [role="button|link|tab|menuitem"]`). Hidden elements (0x0 rect) are filtered out. **Selectors are unique** and can be passed directly to `click_element`. Use this to understand what is on the page before acting.

---

#### `navigate`
**Params:** `url` (required, string)
**CLI:** `.venv/bin/python cli/main.py autopilot browser navigate url=https://example.com`
**Response:** `{ navigated: true, url: string, tabId: number }`

---

#### `click_element`
**Params:** `selector` (required, CSS selector string)
**CLI:** `.venv/bin/python cli/main.py autopilot browser click_element selector=#my-btn`
**Response:** `{ clicked: true, tagName: string, id: string|null, textContent: string (max 100) }` or `{ clicked: false, error: string }`
**Tip:** Selectors from `query_elements` and `get_page_summary` are unique and can be passed directly to this command.

---

#### `click_at`
**Params:** `x` (required, number), `y` (required, number) — coordinates from `query_elements` rect output (compute center: `rect.x + rect.width/2`, `rect.y + rect.height/2`)
**CLI:** `.venv/bin/python cli/main.py autopilot browser click_at x=533 y=1741` or `click_at 533 1741`
**Behavior:** Finds the element at the given coordinates and clicks it. Handles out-of-viewport elements by scrolling them into view first. Falls back to brute-force element search if `elementFromPoint` fails.
**Response:** `{ clicked: true, tagName: string, id: string|null, textContent: string (max 100), x: number, y: number, method: string }` or `{ clicked: false, error: string }`
**Use case:** Best for custom widgets (date pickers, calendar grids, canvas UIs) where elements lack unique CSS selectors or data attributes.

---

#### `click_by_text`
**Params:** `text` (required, string — case-insensitive partial match), `exact` (optional, bool, default false — when true, matches full trimmed text instead of substring)
**CLI:** `.venv/bin/python cli/main.py autopilot browser click_by_text "Sign In"` or `click_by_text "Done" exact=true`
**Searches:** `a, button, [role="button"], input[type="submit"], input[type="button"]` — only visible elements (minimum 10x10px, skip-navigation links are filtered out).
**Response:** `{ clicked: true, tagName: string, text: string }` or `{ clicked: false, error: string }`

---

#### `type_text`
**Params:** `selector` (required), `text` (required), `clearFirst` (optional, bool, default `true`)
**CLI:** `.venv/bin/python cli/main.py autopilot browser type_text selector=#email text=user@example.com`
**Behavior:** Focuses the element, optionally clears it, sets `.value`, dispatches `input` + `change` events.
**Response:** `{ typed: true, tagName: string, id: string|null, finalValue: string (max 200) }` or `{ typed: false, error: string }`

---

#### `type_into_label`
**Params:** `label` (required, string — matched against placeholder, aria-label, name, or `<label>` text, case-insensitive), `text` (required)
**CLI:** `.venv/bin/python cli/main.py autopilot browser type_into_label label=Email text=user@example.com`
**Searches:** `input, textarea, select` elements. Matches the first element whose placeholder, aria-label, name attribute, or associated `<label>` contains the search string.
**Response:** `{ typed: true, tagName: string, name: string, id: string }` or `{ typed: false, error: string }`

---

#### `select_option`
**Params:** `selector` (required, CSS selector for `<select>`), `value` (required — exact value or case-insensitive text match)
**CLI:** `.venv/bin/python cli/main.py autopilot browser select_option selector=#country value=US`
**Response:** `{ selected: true, value: string, text: string }` or `{ selected: false, error: string }`

---

#### `press_key`
**Params:** `key` (required, string — e.g. `Enter`, `Tab`, `Escape`, `ArrowDown`), `selector` (optional — targets `document.activeElement` if omitted)
**CLI:** `.venv/bin/python cli/main.py autopilot browser press_key key=Enter`
**Behavior:** Dispatches `keydown` + `keyup` (and `keypress` for Enter) on the target element.
**Response:** `{ pressed: true, key: string, tagName: string }` or `{ pressed: false, error: string }`

---

#### `wait_for_selector`
**Params:** `selector` (required), `timeout` (optional, ms, default 10000)
**CLI:** `.venv/bin/python cli/main.py autopilot browser wait_for_selector selector=.success-msg`
**Response:** `{ found: bool, waited: number (ms), timeout?: true }`

---

#### `wait_for_url`
**Params:** `url_substring` (required), `timeout` (optional, ms, default 10000)
**CLI:** `.venv/bin/python cli/main.py autopilot browser wait_for_url url_substring=/dashboard`
**Response:** `{ matched: bool, url: string, waited: number (ms), timeout?: true }`

---

#### `query_elements`
**Params:** `selector` (required, CSS selector), `includeText` (optional, bool, default `true`), `maxResults` (optional, number, default 20)
**CLI:** `.venv/bin/python cli/main.py autopilot browser query_elements selector="input, button"`
**Response:** `{ count: number (total matched), returned: number, elements: [{ index, tagName, id, className, type, name, href, value (max 200), placeholder, disabled, visible, rect: {x,y,width,height}, selector, textContent? (max 200) }] }`
Use this for precise element discovery when `get_page_summary` is not enough. **Selectors are unique** — each returned `selector` value can be passed directly to `click_element` to target that exact element. The `rect` coordinates can also be used with `click_at` (compute center: `rect.x + rect.width/2`, `rect.y + rect.height/2`).

---

#### `take_screenshot`
**Params:** none
**CLI:** `.venv/bin/python cli/main.py autopilot browser take_screenshot`
**Response:** `{ screenshot_id: string, url: string, file_path: string, image_url: string }`
Uploads the screenshot to the backend. The `file_path` field contains the local disk path to the saved PNG — use this path to read the screenshot. Use sparingly.
**⚠ Not available in Tabby CDP-streaming mode** — returns HTTP 500 (the worker's `/execute/browser` command set doesn't include it). Extension mode only. In Tabby mode, read the page with `get_page_summary` / `query_elements` / `cdp_get_accessibility_tree` instead.

---

#### `eval_js`
**Params:** `code` (required, string — JavaScript code to evaluate)
**CLI:** `.venv/bin/python cli/main.py autopilot browser eval_js code="return document.title"`
**Response:** `{ success: true, result: any }` or `{ success: false, error: string }`
**Note:** Blocked by CSP on many sites (Google, etc.). Prefer other commands when possible.

---

### CDP Commands (OS-Level Input)

These commands use Chrome DevTools Protocol to dispatch **OS-level input events** — the same mechanism Playwright/Puppeteer use. They work with React, Vue, Angular, custom date pickers, autocomplete fields, and any framework. **Prefer these over the standard commands for all interactions on modern web apps.**

A "debugging" banner will appear in Chrome when CDP commands are first used — this is expected.

---

#### `cdp_click`
**Params:** `selector` (CSS selector) OR `x`/`y` (coordinates). Provide one or both.
**CLI:** `.venv/bin/python cli/main.py autopilot browser cdp_click selector=#search-btn`
**CLI (coordinates):** `.venv/bin/python cli/main.py autopilot browser cdp_click x=400 y=300`
**Behavior:** Scrolls element into view, dispatches real mousePressed/mouseReleased events at element center. Works with React onClick, Vue @click, custom dropdown triggers, date picker buttons, etc.
**Response:** `{ clicked: true, x: number, y: number, selector: string|null }` or `{ clicked: false, error: string }`

**When to use:** Always prefer `cdp_click` over `click_element` and `click_by_text` on React/Vue/Angular sites, custom components, date pickers, and any element where `click_element` fails silently.

---

#### `cdp_type`
**Params:** `selector` (optional CSS selector), `text` (required), `clearFirst` (optional bool, default false)
**CLI:** `.venv/bin/python cli/main.py autopilot browser cdp_type selector=#location text="New York"`
**Behavior:** Clicks to focus the element, optionally clears with Ctrl+A then Backspace, then types each character individually via keyboard events. This triggers React onChange, autocomplete keystroke handlers, input validation, and works on contenteditable elements.
**Response:** `{ typed: true, length: number, selector: string|null }` or `{ typed: false, error: string }`

**When to use:** Always prefer `cdp_type` over `type_text` and `type_into_label` for:
- Autocomplete/typeahead fields (the character-by-character typing triggers search suggestions)
- React/Vue controlled inputs (native keyboard events update React state properly)
- Contenteditable elements (rich text editors, comment boxes)
- Masked inputs (phone numbers, credit cards, dates)

---

#### `cdp_get_accessibility_tree`
**Params:** `maxDepth` (optional, default 10), `interactiveOnly` (optional bool, default true)
**CLI:** `.venv/bin/python cli/main.py autopilot browser cdp_get_accessibility_tree`
**Behavior:** Returns the full accessibility tree from the browser. Pierces shadow DOM and iframes automatically. No element limit. Each node includes: role, name, value, description, focused, disabled, backendDOMNodeId.
**Response:** `{ url: string, title: string, total: number, interactive: number, elements: [{ nodeId, role, name, value, description, focused, disabled, backendDOMNodeId }] }`

**When to use:**
- When `get_page_summary` returns too few elements or misses expected UI components
- On pages with shadow DOM (Material UI, Salesforce, etc.)
- On pages with iframes (embedded forms, payment widgets)
- To discover the semantic structure of complex pages (what roles, labels, and states are present)

---

#### `cdp_press_key`
**Params:** `key` (required — e.g. `Enter`, `Tab`, `ArrowDown`, `Escape`), `modifiers` (optional number: 1=Alt, 2=Ctrl, 4=Meta, 8=Shift)
**CLI:** `.venv/bin/python cli/main.py autopilot browser cdp_press_key key=Enter`
**CLI (with modifier):** `.venv/bin/python cli/main.py autopilot browser cdp_press_key key=a modifiers=2` (Ctrl+A)
**Behavior:** Dispatches OS-level key events that trigger framework handlers, page shortcuts, and default browser behaviors (unlike the DOM-level `press_key`).
**Response:** `{ pressed: true, key: string, modifiers: number }`

---

### Scroll Commands

These commands let you scroll the page or specific containers to discover offscreen elements, trigger lazy loading, and navigate long pages.

---

#### `scroll_page`
**Params:** `direction` (required: `up`, `down`, `left`, `right`), `amount` (optional: `page` (default), `half`, or pixel count)
**CLI:** `.venv/bin/python cli/main.py autopilot browser scroll_page direction=down`
**CLI (half page):** `.venv/bin/python cli/main.py autopilot browser scroll_page direction=down amount=half`
**CLI (pixels):** `.venv/bin/python cli/main.py autopilot browser scroll_page direction=down amount=300`
**Behavior:** Dispatches a native mouse wheel event at viewport center via CDP. Triggers infinite-scroll listeners and lazy-load observers. Waits 350ms for content to settle.
**Response:** `{ scrolled: true, direction, deltaX, deltaY, scrollX, scrollY, scrollWidth, scrollHeight, atTop, atBottom }`

**When to use:** Scrolling the main page to reveal more content, trigger lazy loading, or navigate search results. Check `atBottom: true` to know you've reached the end.

---

#### `scroll_element`
**Params:** `selector` (required: CSS selector for scrollable container), `direction` (required: `up`, `down`, `left`, `right`), `amount` (optional: `page` (default), `half`, or pixel count)
**CLI:** `.venv/bin/python cli/main.py autopilot browser scroll_element selector=.results-list direction=down`
**Behavior:** Finds the container (piercing shadow DOM), calls `scrollBy()`, dispatches a scroll event for lazy-load listeners.
**Response:** `{ scrolled: true, selector, direction, scrollTop, scrollLeft, scrollHeight, scrollWidth, clientHeight, clientWidth }` or `{ scrolled: false, error: string }`

**When to use:** Scrolling within sidebars, modal bodies, dropdown lists, chat panels, or any scrollable container that isn't the main page. Compare `scrollTop + clientHeight` vs `scrollHeight` to know if you've reached the bottom.

---

#### `scroll_to_element`
**Params:** `selector` (required: CSS selector), `block` (optional: `center` (default), `start`, `end`, `nearest`)
**CLI:** `.venv/bin/python cli/main.py autopilot browser scroll_to_element selector=#submit-btn`
**Behavior:** Finds the element (piercing shadow DOM) and scrolls it into view. Returns the element's bounding rect after scroll.
**Response:** `{ scrolled: true, selector, tagName, text, rect: { x, y, width, height } }` or `{ scrolled: false, error: string }`

**When to use:** When you know the selector of an offscreen element and want to bring it into view before interacting with it.

---

### Recommended Command Strategy for Modern SPAs

For sites like Expedia, Salesforce, HubSpot, or any React/Vue SPA:

1. **Read the page:** Start with `get_page_summary`. If it shows too few elements or misses expected UI, use `cdp_get_accessibility_tree` instead.
2. **Click:** Use `cdp_click` or `click_at` for reliable clicking. They fire real mouse events that all frameworks respond to.
3. **Type:** Use `cdp_type` (not `type_text`). Character-by-character typing triggers autocomplete, search-as-you-type, and React state updates.
4. **Select from autocomplete/dropdown:** After typing with `cdp_type`, wait 1-2s, then use `cdp_press_key key=ArrowDown` + `cdp_press_key key=Enter` to select from suggestions. Or use `cdp_get_accessibility_tree` to find the suggestion items and `cdp_click` on them.
5. **Navigate date pickers:** `cdp_click` to open, `cdp_click` on month/year arrows, `cdp_click` on the target day. Or use `click_at` with coordinates from `query_elements`.
6. **Keys:** Use `cdp_press_key` instead of `press_key` when standard `press_key` fails.
7. **Scroll to discover more:** If `get_page_summary` shows `total > returned`, use `scroll_page direction=down` then re-query to find more elements.

---

### Handling Long Pages and Scrollable Containers

**Long pages / search results:**
1. Run `get_page_summary` — check if `total > returned` (elements were cut off)
2. `scroll_page direction=down` to reveal more content
3. Wait 1-2s for lazy-loaded content to appear
4. Run `get_page_summary` again to see the new elements
5. Repeat until you find what you need or `atBottom` is true

**Infinite scroll (e.g. social feeds, search results):**
1. `scroll_page direction=down` — this triggers scroll event listeners that load more content
2. Wait 2-3s for new items to load
3. `get_page_summary` to check for new elements
4. Repeat as needed — stop when no new elements appear or you find what you need

**Scrollable containers (sidebars, modals, dropdowns):**
1. Identify the container selector (use `query_elements` or `cdp_get_accessibility_tree`)
2. `scroll_element selector=<container> direction=down`
3. Re-query elements in the container to see what appeared
4. Check `scrollTop + clientHeight >= scrollHeight` to know if you've reached the bottom

**Known element offscreen:**
- Use `scroll_to_element selector=<target>` to bring it into view, then interact with it

---

## Decision Flow

```
Start
  |
  +-- Backend running? --> No --> Step 1: start
  |                    --> Yes --> continue
  |
  Step 0: Gather website URL, credentials, task description
  |
  Step 1.5: autopilot verify-extension
  |   +-- UNSUPPORTED? --> Ask user to reload extension, re-run
  |
  Step 2: autopilot start-capture "<name>" "<url>"
  |        --> note workflow_session_id + capture_session_id
  |
  Step 3: Navigate to website
  |
  +-- Login needed? --> Yes --> Step 4: Login flow
  |                 --> No  --> continue
  |
  Step 5: Perform task (read page, decide, act, repeat)
  |
  +-- Complex UI? --> Use strategies from "Handling Complex UIs"
  +-- Action failed? --> Follow "Retry and Fallback Strategy"
  +-- Extension reloaded? --> stop-capture, then resume-capture <wf_id>
  +-- MFA/captcha? --> Ask user to complete, wait, resume
  +-- Dangerous button? --> Check allowed list, ask if unsure
  |
  Step 6: autopilot stop-capture <wf_id> <cs_id>
  |   +-- No HAR? --> Try resume-capture <wf_id>, or /noui-record-workflow
  |
  Step 7: autopilot export <wf_id> <cs_id>
  |   +-- 0 tools? --> Warn user, site may use server-side rendering
  |
  Step 8: Install + report
```

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| Browser command returns 504 timeout | Extension not connected — ask user to open Chrome with the NoUI extension |
| `get_page_summary` shows no elements | Page may still be loading — wait 2-3s and retry |
| HAR file not found after stop | Extension may not have uploaded — wait longer, check `workflow captures` |
| 0 tools after export | The workflow only recorded HTML pages, not API calls — the site may use server-side rendering |
| Login redirect loop | The session cookie may not persist — check if the extension is on the same tab |
| `click_by_text` says "not found" | Run `get_page_summary` to see exact element text, use `click_element` with unique selector or `click_at` with coordinates |
| `click_by_text` matched wrong element | Use `exact=true` for exact text matching: `click_by_text "Done" exact=true` |
| Calendar/grid cells have no unique selectors | Use `query_elements` — selectors are now unique. Or use `click_at` with rect coordinates |
| Autocomplete dropdown not visible in `get_page_summary` | Use `query_elements` with `[role="option"]` or `[role="listbox"]` selectors, or `take_screenshot` to see the dropdown |
| Date picker not responding to `type_text` | Click the date field first to open the picker, then navigate using the picker's UI controls |
| `select_option` fails on a dropdown | The dropdown is likely a custom widget, not a `<select>`. Click it to open, then `click_by_text` on the desired option |
| `eval_js` returns CSP error | Use `click_element`, `type_text`, `press_key` instead — these go through the extension content script |
| Page content not updating after click | SPA transition in progress — use `wait_for_selector` or wait 2-3s and re-check with `get_page_summary` |
| `click_element` doesn't work on React/Vue site | Use `cdp_click` or `click_at` instead — they fire OS-level mouse events that all frameworks respond to |
| `type_text` doesn't trigger autocomplete suggestions | Use `cdp_type` instead — it types character-by-character, triggering keystroke handlers and search-as-you-type |
| Chrome shows "debugging" banner | Normal — CDP commands require the debugger API. The banner disappears when commands stop |
| Element exists but not in `get_page_summary` | It may be offscreen — use `scroll_page direction=down` and re-query, or `scroll_to_element` if you know the selector |
| Infinite scroll page doesn't load more items | Use `scroll_page` (CDP wheel events) instead of keyboard-based scrolling — it triggers scroll event listeners |
| Can't scroll inside a modal/sidebar | Use `scroll_element selector=<container>` targeting the scrollable container, not `scroll_page` |
| Extension reloaded mid-capture, HAR lost | Capture is dead. Run `autopilot stop-capture`, then `autopilot resume-capture <wf_id>` to start a fresh capture on the same workflow |
| `verify-extension` shows UNSUPPORTED commands | Extension is stale. Reload it in `chrome://extensions`, then re-run `verify-extension` |

---

## CLI Command Reference

| Command | Description |
|---------|-------------|
| `.venv/bin/python cli/main.py start` | Start backend |
| `.venv/bin/python cli/main.py status` | Check backend health |
| `.venv/bin/python cli/main.py autopilot verify-extension` | Pre-flight check: verify extension supports all browser commands |
| `.venv/bin/python cli/main.py autopilot start-capture <name> <url>` | Create workflow + capture sessions and start recording |
| `.venv/bin/python cli/main.py autopilot capture-status <cs_id>` | Show live status of a capture session (status, HAR presence) |
| `.venv/bin/python cli/main.py autopilot stop-capture <wf_id> <cs_id>` | Stop capture, complete workflow, wait for HAR |
| `.venv/bin/python cli/main.py autopilot resume-capture <wf_id>` | Create new capture session on existing workflow (after broken capture) |
| `.venv/bin/python cli/main.py autopilot export <wf_id> <cs_id>` | Validate HAR and export MCP server |
| `.venv/bin/python cli/main.py autopilot browser <cmd> [args]` | Execute browser command |
| `.venv/bin/python cli/main.py autopilot list` | List autopilot runs |
| `.venv/bin/python cli/main.py autopilot status <run_id>` | Show run details |
| `.venv/bin/python cli/main.py mcp install <server_id> claude-code` | Install MCP server |
