"""Regression test for operation_generator.py's object/array body-param handling.

A GraphQL-style `variables` body field is typed "object" by
har_to_tools.py::_body_to_params. The Skill CLI can only ever hand it in as a
JSON-encoded string (argparse has no object type), so the generated
`execute()` must json.loads() it before building the request body — forwarding
it unparsed double-encodes it on the wire and most servers reject it. This is
a real bug found compiling a QuickBooks Online workflow (GraphQL `variables`).
"""

from __future__ import annotations

from noui_core.compile.operation_generator import render_skill_operation


def _tool_def(**overrides) -> dict:
    base = {
        "name": "create_graphql",
        "method": "POST",
        "path": "/graphql",
        "base_url": "https://api.example.com",
        "description": "Post Graphql",
        "request_content_type": "application/json",
        "request_headers": [],
        "params": [
            {"name": "query", "type": "string", "required": True, "source": "body"},
            {"name": "variables", "type": "object", "required": True, "source": "body"},
        ],
    }
    base.update(overrides)
    return base


class TestObjectBodyParamCodegen:
    def test_object_param_is_json_loaded_before_body_construction(self) -> None:
        src = render_skill_operation(_tool_def(), auth_plan={})
        assert "json.loads(variables)" in src
        # The other (string) param must NOT be parsed.
        assert "json.loads(query)" not in src

    def test_generated_source_compiles(self) -> None:
        src = render_skill_operation(_tool_def(), auth_plan={})
        compile(src, "<generated>", "exec")

    def test_cli_help_flags_json_encoding_for_object_param(self) -> None:
        src = render_skill_operation(_tool_def(), auth_plan={})
        assert "(JSON-encoded)" in src

    def test_string_only_params_unaffected(self) -> None:
        td = _tool_def(
            params=[
                {"name": "query", "type": "string", "required": True, "source": "body"},
            ]
        )
        src = render_skill_operation(td, auth_plan={})
        assert "json.loads" not in src


def _whole_body_tool_def(**overrides) -> dict:
    base = {
        "name": "create_refreshstepcount",
        "method": "POST",
        "path": "/companies/SearchExpert/RefreshStepCount",
        "base_url": "https://tpcatalyst-r1.bvdinfo.com",
        "description": "Refresh step count",
        "request_content_type": "application/bvdjson",
        "request_headers": [],
        "params": [
            {
                "name": "body",
                "type": "array",
                "required": True,
                "source": "body",
                "whole_body": True,
            },
        ],
    }
    base.update(overrides)
    return base


class TestWholeBodyArrayCodegen:
    """A top-level JSON array IS the body — it must not be wrapped in a dict.

    Wrapping would put `{"body": [...]}` on the wire where the server expects
    `[...]`, which it reads as a different (and empty) request. TP Catalyst's
    `application/bvdjson` search payload is the case this was found on; see
    har_to_tools.py::_body_to_params.
    """

    def test_body_is_assigned_directly_not_wrapped_in_a_dict(self) -> None:
        src = render_skill_operation(_whole_body_tool_def(), auth_plan={})
        assert "body = json.loads(body)" in src
        # The regression: a dict wrapper puts the array under a "body" key.
        assert "{'body': json.loads(body)}" not in src

    def test_body_is_passed_to_execute_fetch(self) -> None:
        # Without this the operation posts nothing at all.
        src = render_skill_operation(_whole_body_tool_def(), auth_plan={})
        assert "body=body" in src

    def test_generated_source_compiles(self) -> None:
        src = render_skill_operation(_whole_body_tool_def(), auth_plan={})
        compile(src, "<generated>", "exec")

    def test_named_body_params_still_build_a_dict(self) -> None:
        # The ordinary path must be untouched by the whole_body branch.
        src = render_skill_operation(_tool_def(), auth_plan={})
        assert "body = {" in src


def _wire_name_tool_def(**overrides) -> dict:
    """A form-encoded body whose field names are not Python identifiers."""
    base = {
        "name": "create_export",
        "method": "POST",
        "path": "/companies/AnalysisSummary/Export",
        "base_url": "https://tpcatalyst-r1.bvdinfo.com",
        "description": "Post Export",
        "request_content_type": "application/x-www-form-urlencoded",
        "request_headers": [],
        "params": [
            {"name": "component.FileName", "type": "string", "required": True, "source": "body"},
            {
                "name": "component.AttachmentTypes%5BReviewSummaryProofs%5D.Selected",
                "type": "string",
                "required": True,
                "source": "body",
            },
            {"name": "2ndTry", "type": "string", "required": False, "source": "query"},
            {"name": "class", "type": "string", "required": False, "source": "query"},
        ],
    }
    base.update(overrides)
    return base


class TestWireNameIsNotAPythonIdentifier:
    """Wire parameter names are not necessarily Python identifiers.

    TP Catalyst's AnalysisSummary/Export posts form fields carrying dots and
    percent-encoded brackets. The codegen used the wire name for BOTH the dict
    key and the Python symbol, so the emitted module did not parse at all —
    the operation could never run. Found 2026-08-18.
    """

    def test_generated_source_parses(self) -> None:
        # The regression itself: the file used to be a SyntaxError.
        src = render_skill_operation(_wire_name_tool_def(), auth_plan={})
        compile(src, "<generated>", "exec")

    def test_wire_name_is_kept_as_the_dict_key(self) -> None:
        # Sanitising the key too would send the server a field it does not know.
        src = render_skill_operation(_wire_name_tool_def(), auth_plan={})
        assert "'component.AttachmentTypes%5BReviewSummaryProofs%5D.Selected':" in src

    def test_python_symbol_is_sanitised(self) -> None:
        src = render_skill_operation(_wire_name_tool_def(), auth_plan={})
        assert "component_FileName" in src
        assert "component.FileName:" not in src  # never as a parameter

    def test_leading_digit_and_keyword_are_handled(self) -> None:
        src = render_skill_operation(_wire_name_tool_def(), auth_plan={})
        compile(src, "<generated>", "exec")
        assert "p_2ndTry" in src  # identifiers may not start with a digit
        assert "class_" in src  # nor be a reserved word

    def test_colliding_wire_names_do_not_produce_duplicate_parameters(self) -> None:
        # "a.b" and "a-b" both sanitise to "a_b"; without a suffix that is a
        # duplicate-argument SyntaxError, which is worse than the original bug.
        td = _wire_name_tool_def(
            params=[
                {"name": "a.b", "type": "string", "required": True, "source": "body"},
                {"name": "a-b", "type": "string", "required": True, "source": "body"},
            ]
        )
        src = render_skill_operation(td, auth_plan={})
        compile(src, "<generated>", "exec")
        assert "a_b_2" in src

    def test_ordinary_names_are_untouched(self) -> None:
        td = _wire_name_tool_def(
            params=[
                {"name": "query", "type": "string", "required": True, "source": "body"},
            ]
        )
        src = render_skill_operation(td, auth_plan={})
        assert "query: str" in src
        assert "p_query" not in src
