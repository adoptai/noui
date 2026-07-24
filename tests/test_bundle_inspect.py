"""bundle_inspect: summarise a capture without writing an ad-hoc script.

Two properties matter. The mode block must make a stamp/classification mismatch
obvious (that is the whole reason this command exists), and the endpoint table
must agree with what the compiler would actually emit — a preview, not a second
opinion. Plus the obvious safety property: never print a recorded value.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from noui_core.capture.inspect import summarize
from noui_core.compile.har_to_tools import har_to_tool_defs

_NOUI_ROOT = Path(__file__).resolve().parent.parent
for _p in (_NOUI_ROOT / "skills" / "noui", _NOUI_ROOT / "skills" / "noui" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bundle_inspect as bi  # noqa: E402

T = {k: f"2026-01-01T00:00:{k:02d}.000Z" for k in range(12)}


def _entry(ts, url, method="GET", status=200, text="{}", mime="application/json", body=None):
    entry = {
        "startedDateTime": ts,
        "request": {"method": method, "url": url},
        "response": {"status": status, "content": {"mimeType": mime, "text": text}},
    }
    if body is not None:
        entry["request"]["postData"] = {"mimeType": "application/json", "text": body}
    return entry


def _bundle(*, stamped="login", clicks=None, urls=None, entries=None) -> dict:
    return {
        "session_id": "sess-abc",
        "recording_mode": stamped,
        "started_at": T[0],
        "stopped_at": T[9],
        "click_events": clicks or [],
        "url_events": urls or [],
        "cookies": [{"name": "sid", "value": "secret-cookie-value"}],
        "har": {"log": {"entries": entries or []}},
    }


def _workflow_bundle() -> dict:
    """A workflow capture stamped 'login' — the production mismatch."""
    return _bundle(
        stamped="login",
        clicks=[{"timestamp": T[3], "tag_name": "button", "text_content": "Listings"}],
        urls=[
            {
                "timestamp": T[2],
                "from_url": "https://airbnb.test/",
                "to_url": "https://airbnb.test/hosting/listings",
            }
        ],
        entries=[
            _entry(T[4], "https://airbnb.test/api/v3/UnifiedListOfListingsQuery", text="x" * 500),
            _entry(T[5], "https://airbnb.test/api/v3/IsHostQuery"),
            _entry(T[6], "https://airbnb.test/analytics/collect?e=1"),  # noise
            _entry(T[7], "https://airbnb.test/static/app.js", mime="application/javascript"),
        ],
    )


class TestModeBlock:
    def test_surfaces_the_stamp_mismatch(self):
        summary = summarize(_workflow_bundle())
        mode = summary["mode"]
        assert mode["tabby_reported"] == "login"
        assert mode["noui_classification"] == "workflow"
        assert mode["tabby_disagrees"] is True

    def test_rendered_output_warns_about_the_unreliable_field(self):
        text = bi.render(summarize(_workflow_bundle()))
        assert "recording_mode (Tabby, IGNORED) : login" in text
        assert "NoUI classification            : workflow" in text
        assert "warm-pool" in text

    def test_ledger_mismatch_names_the_override_flag(self):
        summary = summarize(_workflow_bundle(), ledger_entry={"declared_mode": "combined"})
        assert summary["mode"]["ledger_disagrees"] is True
        text = bi.render(summary)
        assert "provisioned as 'combined'" in text
        assert "--mode workflow" in text

    def test_no_ledger_is_stated_not_hidden(self):
        text = bi.render(summarize(_workflow_bundle()))
        assert "no ledger entry" in text

    def test_agreement_produces_no_warnings(self):
        summary = summarize(
            _workflow_bundle() | {"recording_mode": "workflow"},
            ledger_entry={"declared_mode": "workflow"},
        )
        assert summary["mode"]["tabby_disagrees"] is False
        assert "⚠" not in bi.render(summary)


class TestEndpointTable:
    def test_matches_what_the_compiler_would_emit(self):
        bundle = _workflow_bundle()
        summary = summarize(bundle)
        compiled = har_to_tool_defs(
            bundle["har"], workflow_name="listings", tabby_profile_id="airbnb"
        )
        assert [e["tool_name"] for e in summary["endpoints"]] == [t["name"] for t in compiled]

    def test_noise_and_static_assets_are_excluded(self):
        summary = summarize(_workflow_bundle())
        paths = [e["path_template"] for e in summary["endpoints"]]
        assert "/analytics/collect" not in paths
        assert "/static/app.js" not in paths
        assert summary["counts"]["har_entries"] == 4
        assert summary["counts"]["api_calls"] == 2

    def test_repeats_are_deduped_and_counted(self):
        bundle = _bundle(
            entries=[
                _entry(T[1], "https://app.test/api/search?q=a"),
                _entry(T[2], "https://app.test/api/search?q=b"),
                _entry(T[3], "https://app.test/api/search?q=c"),
            ]
        )
        endpoints = summarize(bundle)["endpoints"]
        assert len(endpoints) == 1
        assert endpoints[0]["occurrences"] == 3

    def test_measures_the_captured_body_when_har_omits_size(self):
        """Tabby's HAR has content.text but no content.size — 0 would read as empty."""
        endpoints = summarize(_workflow_bundle())["endpoints"]
        by_name = {e["path_template"]: e for e in endpoints}
        assert by_name["/api/v3/UnifiedListOfListingsQuery"]["size"] == 500

    def test_flags_operations_that_send_a_body(self):
        bundle = _bundle(
            entries=[_entry(T[1], "https://app.test/api/graphql", method="POST", body='{"q":1}')]
        )
        assert summarize(bundle)["endpoints"][0]["has_request_body"] is True

    def test_empty_har_does_not_explode(self):
        summary = summarize(_bundle(entries=[]))
        assert summary["endpoints"] == []
        assert "nothing here looks like an API call" in bi.render(summary)


class TestTimeline:
    def test_reads_from_url_and_to_url_not_url(self):
        """url_events carry from_url/to_url; click_events carry a flat url."""
        summary = summarize(_workflow_bundle())
        assert summary["url_timeline"] == [
            {
                "timestamp": T[2],
                "from_url": "https://airbnb.test/",
                "to_url": "https://airbnb.test/hosting/listings",
                "is_login_boundary": False,
                "post_login": False,
            }
        ]

    def test_marks_the_login_boundary(self):
        bundle = _bundle(
            clicks=[
                {"timestamp": T[1], "tag_name": "input", "field_role": "username"},
                {"timestamp": T[2], "tag_name": "input", "field_role": "password"},
            ],
            urls=[
                {
                    "timestamp": T[3],
                    "from_url": "https://app.test/login",
                    "to_url": "https://app.test/home",
                },
                {
                    "timestamp": T[5],
                    "from_url": "https://app.test/home",
                    "to_url": "https://app.test/reports",
                },
            ],
            entries=[_entry(T[6], "https://app.test/api/reports")],
        )
        timeline = summarize(bundle)["url_timeline"]
        assert [(e["is_login_boundary"], e["post_login"]) for e in timeline] == [
            (True, False),
            (False, True),
        ]
        assert "login boundary" in bi.render(summarize(bundle))


class TestSafety:
    def test_never_emits_recorded_values(self):
        bundle = _bundle(
            clicks=[
                {
                    "timestamp": T[1],
                    "tag_name": "input",
                    "field_role": "password",
                    "value": "hunter2-should-never-print",
                    "is_redacted": False,
                },
            ],
            entries=[_entry(T[2], "https://app.test/api/me", text='{"token":"leaky-token"}')],
        )
        text = bi.render(summarize(bundle))
        assert "hunter2-should-never-print" not in text
        assert "leaky-token" not in text
        assert "secret-cookie-value" not in text

    def test_credential_roles_are_counted_with_redaction_status(self):
        bundle = _bundle(
            clicks=[
                {"timestamp": T[1], "tag_name": "input", "field_role": "username"},
                {"timestamp": T[2], "tag_name": "input", "field_role": "otp", "is_redacted": True},
                {"timestamp": T[3], "tag_name": "input", "field_role": "otp", "is_redacted": True},
            ]
        )
        assert summarize(bundle)["credential_events"] == [
            {"field_role": "otp", "count": 2, "redacted": 2},
            {"field_role": "username", "count": 1, "redacted": 0},
        ]

    def test_unredacted_secrets_are_flagged_loudly(self):
        bundle = _bundle(
            clicks=[
                {
                    "timestamp": T[1],
                    "tag_name": "input",
                    "field_role": "password",
                    "value": "plaintext",
                    "is_redacted": False,
                }
            ]
        )
        summary = summarize(bundle)
        assert summary["unredacted_secrets"] == 1
        assert "capture_import will refuse this bundle" in bi.render(summary)


class TestCli:
    def test_reads_a_bundle_from_disk(self, tmp_path, capsys, monkeypatch):
        import json

        from noui_core.config import settings

        monkeypatch.setattr(settings, "workbench_dir", str(tmp_path))
        path = tmp_path / "b.json"
        path.write_text(json.dumps(_workflow_bundle()))
        monkeypatch.setattr(sys, "argv", ["bundle_inspect.py", str(path)])
        assert bi.main() == 0
        assert "NoUI classification            : workflow" in capsys.readouterr().out

    def test_json_mode_is_machine_readable(self, tmp_path, capsys, monkeypatch):
        import json

        from noui_core.config import settings

        monkeypatch.setattr(settings, "workbench_dir", str(tmp_path))
        path = tmp_path / "b.json"
        path.write_text(json.dumps(_workflow_bundle()))
        monkeypatch.setattr(sys, "argv", ["bundle_inspect.py", str(path), "--json"])
        assert bi.main() == 0
        parsed = json.loads(capsys.readouterr().out)
        assert parsed["mode"]["noui_classification"] == "workflow"

    def test_missing_file_is_an_error_not_a_traceback(self, tmp_path, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["bundle_inspect.py", str(tmp_path / "nope.json")])
        assert bi.main() == 1
        assert "Could not read bundle" in capsys.readouterr().err

    def test_requires_an_argument(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["bundle_inspect.py"])
        with pytest.raises(SystemExit):
            bi.main()
