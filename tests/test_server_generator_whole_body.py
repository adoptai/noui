"""The MCP compiler must not wrap a top-level JSON array body in a dict.

`server_generator.py` keeps its own copy of the body-rendering helpers,
"duplicated from operation_generator.py ... to avoid a cross-compiler private
import; small enough to keep in sync by hand". The hand-sync missed the
`whole_body` branch, so the MCP path emitted

    body = {'body': json.loads(body) if isinstance(body, str) else body}

putting `{"body": [...]}` on the wire where the server expects `[...]` — the
same defect this PR fixes for the Skill path. Found 2026-08-19 by compiling the
TP Catalyst bundle with `--as mcp`.
"""

from __future__ import annotations

from noui_core.compile.server_generator import _render_body_assignment


def _whole() -> list[dict]:
    return [
        {"name": "body", "type": "array", "required": True, "source": "body", "whole_body": True}
    ]


def _named() -> list[dict]:
    return [
        {"name": "query", "type": "string", "required": True, "source": "body"},
        {"name": "variables", "type": "object", "required": True, "source": "body"},
    ]


class TestMcpWholeBodyArray:
    def test_array_body_is_not_wrapped_in_a_dict(self) -> None:
        line = _render_body_assignment(_whole())
        assert "{'body':" not in line  # the regression
        assert line.strip().startswith("body = json.loads(body)")

    def test_stays_defensive_about_already_parsed_args(self) -> None:
        # An MCP client may hand the arg in already parsed; _body_dict_entry is
        # defensive for the same reason, so the whole-body branch must be too.
        # Asserted on the WHOLE line: the dict form also contains this guard,
        # so a substring check would pass against the very defect under test.
        assert _render_body_assignment(_whole()).strip() == (
            "body = json.loads(body) if isinstance(body, str) else body"
        )

    def test_data_var_is_honoured_for_non_json_content_types(self) -> None:
        # `data = {...}` also starts with "data = ", so pin the passthrough form.
        assert _render_body_assignment(_whole(), "data").strip() == (
            "data = json.loads(body) if isinstance(body, str) else body"
        )

    def test_named_params_still_build_a_dict(self) -> None:
        line = _render_body_assignment(_named())
        assert line.strip().startswith("body = {")
        assert "'query':" in line and "'variables':" in line
