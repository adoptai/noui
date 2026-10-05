# Contributing to NoUI

Thanks for your interest in contributing. This document covers how to get a development environment running, repository conventions, and how to bump the pinned Tabby dependency.

## Development setup

NoUI depends on a specific revision of [Tabby](https://github.com/adoptai/tabby), included as a git submodule.

```bash
# Clone with submodules
git clone --recursive https://github.com/adoptai/noui.git
cd noui

# If you already cloned without --recursive:
git submodule update --init

# Install dependencies
python3 -m venv .venv
.venv/bin/python -m pip install poetry
.venv/bin/python -m poetry install --no-root --with lint,test

# Configure environment (the toolkit reads skills/noui/.env or the process env)
cp skills/noui/.env.example skills/noui/.env
# Edit it: TABBY_API_URL, TABBY_CLIENT_ID / TABBY_CLIENT_SECRET (+ TABBY_ADMIN_TOKEN to
# register App Templates). No LLM key is needed: compile is deterministic.
```

### Running against a local Tabby checkout

If you are actively developing against Tabby and want to point NoUI at a sibling
checkout instead of the pinned submodule, set the `TABBY_DIR` environment
variable:

```bash
export TABBY_DIR="$HOME/code/tabby"
```

This is a contributor convenience only. Release testing and the Validation
Plan always use the pinned submodule.

## Branch and PR conventions

- Branches: `feat/`, `fix/`, `chore/`, `docs/`, `refactor/` prefixes.
- Keep commits small and scoped to a single coherent change.
- Open a pull request for anything non-trivial. Use draft PRs when early feedback helps.
- PR descriptions should cover what changed, why, and how it was validated.
- Do not commit secrets, `.env` files, session cookies, or customer HAR payloads.

## Running tests and checks

```bash
poetry run ruff check .
poetry run ruff format --check .
poetry run mypy .
poetry run pytest -v
```

CI runs the same commands on every pull request.

## Bumping the Tabby submodule

NoUI pins a specific SHA on Tabby's `tabby-noui` branch. To move that pin:

1. Land the target change on Tabby's `tabby-noui` branch.
2. In NoUI, update the submodule:
   ```bash
   cd tabby
   git fetch origin tabby-noui
   git checkout <target-sha>
   cd ..
   git add tabby
   ```
3. Run the full compat test matrix (see [README.md](README.md#tabby-compatibility)).
4. Update the Docker image digest referenced in the compose file if the runtime image changed.
5. Add a `CHANGELOG.md` entry under "Unreleased" describing the bump.
6. Open a PR.

## SPDX headers

New source files should include an SPDX identifier at the top:

```python
# SPDX-License-Identifier: MIT
```

Back-filling existing files is not required.

## Code of conduct

By participating, you agree to abide by the [Code of Conduct](CODE_OF_CONDUCT.md).
