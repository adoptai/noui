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
