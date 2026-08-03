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


def _frontmatter_block(md):
    """The raw YAML frontmatter (between the first two --- fences)."""
    assert md.startswith("---\n")
    return md.split("---\n", 2)[1]


def test_static_secret_skill_declares_auth_and_api_hosts():
    # The harness reads api_hosts frontmatter to route this call server-side
    # (direct, non-browser) — the fix for the cross-origin CORS failure.
    fm = _frontmatter_block(_render(_STATIC_PLAN))
    assert "\nauth: api-key\n" in fm
    assert "\napi_hosts:\n  - www.airbnb.com\n" in fm


def test_session_auth_skill_declares_no_direct_routing():
    # A browser/session skill must NOT be marked for server-side routing.
    fm = _frontmatter_block(_render(_SESSION_PLAN))
    assert "auth: api-key" not in fm
    assert "api_hosts:" not in fm


def test_static_secret_description_has_no_tabby_session_caveat():
    # The synthesized description must not claim an api-key skill needs a Tabby
    # sign-in — it authenticates server-side with a static key.
    desc = _frontmatter_block(_render(_STATIC_PLAN)).split("description:", 1)[1]
    assert "authenticated Tabby session" not in desc
    assert "static API key" in desc


def test_session_description_keeps_tabby_session_caveat():
    desc = _frontmatter_block(_render(_SESSION_PLAN)).split("description:", 1)[1]
    assert "authenticated Tabby session" in desc


def test_api_hosts_dedupes_across_operations_in_order():
    from noui_core.compile.harness_md_generator import api_hosts_for

    tds = [
        {"name": "a", "method": "GET", "path": "/p", "base_url": "https://auth.example.com"},
        {"name": "b", "method": "GET", "path": "/q", "base_url": "https://api.example.com"},
        {"name": "c", "method": "GET", "path": "/r", "base_url": "https://api.example.com"},
    ]
    assert api_hosts_for(tds) == ["auth.example.com", "api.example.com"]
