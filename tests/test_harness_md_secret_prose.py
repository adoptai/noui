"""Regression: the generated harness SKILL.md must only mention ${SECRET:...}
when the skill actually needs a static secret. For a session-auth skill the
prose example used to leak a bare ${SECRET:...} token, which downstream secret
scanners (install_skill) mistook for a required secret named '...'."""

from noui_core.compile.harness_md_generator import render_harness_skill_md

_TOOL_DEFS = [
    {
        "name": "create_api_v3_stayssearch",
        "method": "GET",
        "path": "/api/v3/StaysSearch",
        "base_url": "https://www.airbnb.com",
        "description": "Search stays",
    }
]

_SESSION_PLAN = {"strategy": "tabby_credentials", "fallbacks": []}
_STATIC_PLAN = {
    "strategy": "static_secret_header",
    "fallbacks": [
        {
            "type": "static_secret_header",
            "header": "Authorization",
            "value_template": "Bearer ${AIRBNB_API_KEY}",
            "secret_env_var": "AIRBNB_API_KEY",
        }
    ],
}


def _render(plan):
    return render_harness_skill_md(
        skill_id="airbnb",
        app_name="Airbnb",
        workflow_name="search",
        tool_defs=_TOOL_DEFS,
        auth_plan=plan,
        profile_slug="airbnb",
    )


def test_session_auth_skill_md_has_no_secret_placeholder():
    md = _render(_SESSION_PLAN)
    assert "${SECRET:" not in md  # no bait for secret scanners, no misleading prose


def test_static_secret_skill_md_keeps_secret_guidance():
    md = _render(_STATIC_PLAN)
    assert "${SECRET:" in md  # a real static-secret skill still documents it
