"""The MCP compiler must sanitise wire names into Python identifiers too.

`server_generator.py` kept its own copy of `_body_dict_entry` / `_py_signature`,
"duplicated from operation_generator.py ... small enough to keep in sync by
hand". Both copies used the wire name as the dict key AND the Python symbol, so
the MCP path emitted modules that did not parse — including `server.py` itself.

Compiling the TP Catalyst bundle with `--as mcp` produced 3 SyntaxErrors before
this fix and 0 after. Raised in review on #145 by @rahulbh1510.
"""

from __future__ import annotations

from noui_core.compile.server_generator import (
    _assign_py_names,
    _body_dict_entry,
    _py_signature,
    _render_operation,
)

HOSTILE = "component.AttachmentTypes%5BReviewSummaryProofs%5D.Selected"


def _params() -> list[dict]:
    p = [
        {"name": HOSTILE, "type": "string", "required": True, "source": "body"},
        {"name": "2ndTry", "type": "string", "required": False, "source": "query"},
        {"name": "class", "type": "string", "required": False, "source": "query"},
    ]
    _assign_py_names(p)
    return p


def _tool_def() -> dict:
    return {
        "name": "create_export",
        "method": "POST",
        "path": "/companies/AnalysisSummary/Export",
        "base_url": "https://tpcatalyst-r1.bvdinfo.com",
        "description": "Post Export",
        "request_content_type": "application/x-www-form-urlencoded",
        "request_headers": [],
        "params": [
            {"name": HOSTILE, "type": "string", "required": True, "source": "body"},
            {"name": "class", "type": "string", "required": False, "source": "query"},
        ],
    }


class TestMcpWireNameSanitisation:
    def test_generated_operation_parses(self) -> None:
        # The regression: this module used to be a SyntaxError.
        src = _render_operation(_tool_def(), auth_plan={})
        compile(src, "<generated>", "exec")

    def test_wire_name_stays_the_dict_key(self) -> None:
        # Sanitising the key too would send the server a field it does not know.
        entry = _body_dict_entry(_params()[0])
        assert entry.startswith(f"{HOSTILE!r}:")

    def test_python_symbol_is_sanitised_in_the_dict_value(self) -> None:
        entry = _body_dict_entry(_params()[0])
        assert "component_AttachmentTypes_5BReviewSummaryProofs_5D_Selected" in entry

    def test_signature_uses_the_sanitised_symbol(self) -> None:
        sig = " ".join(_py_signature(_params()))
        assert HOSTILE not in sig
        assert "p_2ndTry" in sig  # may not start with a digit
        assert "class_" in sig  # nor be a reserved word

    def test_ordinary_names_are_untouched(self) -> None:
        p = [{"name": "query", "type": "string", "required": True, "source": "body"}]
        _assign_py_names(p)
        assert _body_dict_entry(p[0]) == "'query': query"
