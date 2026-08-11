"""The login template's post-login check is the page LOGIN lands on.

A login lands you somewhere by redirect; everything after is the human clicking.
The old rule stopped at the first ORIGIN CHANGE, written for a multi-host portal
— and on a single-origin app there is never one, so it walked to the end of a
combined recording and took wherever the human finished. ICICI retail compiled
`/credit-card**`, a page four clicks into the workflow, as its post-login check.
"""

from noui_core.compile.login_assets import _before_first_click

BASE = "https://retailnetbanking.icici.bank.in"


def url(seq, frm, to):
    return {"seq": seq, "from_url": frm, "to_url": to}


def click(seq):
    return {"seq": seq, "event_type": "click"}


def test_the_login_segment_stops_at_the_first_click():
    # login -> overview happens by redirect; everything after is navigation.
    url_events = [
        url(0, "", f"{BASE}/login-page"),
        url(1, f"{BASE}/login-page", f"{BASE}/overview"),
        url(5, f"{BASE}/overview", f"{BASE}/credit-card"),
        url(9, f"{BASE}/credit-card", f"{BASE}/credit-card/statements"),
    ]
    transitions = [(u["from_url"], u["to_url"]) for u in url_events]
    kept = _before_first_click(transitions, url_events, [click(3), click(7)])

    assert [t[1] for t in kept] == [f"{BASE}/login-page", f"{BASE}/overview"]
    # The workflow pages are gone, so /credit-card can never become the check.
    assert all("credit-card" not in t[1] for t in kept)


def test_a_login_that_settles_through_several_redirects_keeps_them_all():
    # Logins commonly bounce once or twice before landing; none of that is a click.
    url_events = [
        url(0, "", f"{BASE}/login-page"),
        url(1, f"{BASE}/login-page", f"{BASE}/mfa"),
        url(2, f"{BASE}/mfa", f"{BASE}/overview"),
    ]
    transitions = [(u["from_url"], u["to_url"]) for u in url_events]
    kept = _before_first_click(transitions, url_events, [click(6)])
    assert len(kept) == 3


def test_a_pure_login_recording_is_unchanged():
    # No clicks recorded at all: nothing to bound, everything is login.
    url_events = [
        url(0, "", f"{BASE}/login-page"),
        url(1, f"{BASE}/login-page", f"{BASE}/overview"),
    ]
    transitions = [(u["from_url"], u["to_url"]) for u in url_events]
    assert _before_first_click(transitions, url_events, []) == transitions


def test_a_click_before_any_navigation_does_not_erase_the_login():
    # A cookie banner dismissed on the login page would otherwise leave nothing,
    # and no landing page at all is worse than one derived the old way.
    url_events = [
        url(4, "", f"{BASE}/login-page"),
        url(5, f"{BASE}/login-page", f"{BASE}/overview"),
    ]
    transitions = [(u["from_url"], u["to_url"]) for u in url_events]
    assert _before_first_click(transitions, url_events, [click(1)]) == transitions


def test_a_recording_without_seq_falls_back():
    # Pre-schema-2 bundles carry no ordering; derive as before rather than guess.
    url_events = [{"from_url": "", "to_url": f"{BASE}/overview"}]
    transitions = [("", f"{BASE}/overview")]
    assert _before_first_click(transitions, url_events, [{"event_type": "click"}]) == transitions


def _takeover_steps(url_events, click_events, *, manual_takeover=True):
    from noui_core.compile import login_assets

    out = login_assets.generate(
        {"id": "s", "app_name": "icici", "login_url": f"{BASE}/login-page"},
        click_events,
        url_events,
        manual_takeover=manual_takeover,
        manual_credentials=True,
    )

    def find(o):
        if isinstance(o, dict):
            if "steps" in o and "login_url" in o:
                return o
            for v in o.values():
                got = find(v)
                if got:
                    return got
        return None

    return (find(out) or {}).get("steps") or []


def test_a_takeover_login_still_finds_the_page_it_landed_on():
    """When the HUMAN is the login, the landing comes after their first click.

    The narrowing rule assumes a redirect lands you. A takeover has the human
    typing and pressing Log In, so narrowing keeps only the login page — which
    is then dropped for being the login page, leaving no landing page at all.
    ICICI compiled a takeover with no wait_for_url, so every session asked a
    human to confirm a login they had already completed.
    """
    url_events = [
        url(1, "about:blank", f"{BASE}/login-page"),
        url(3, f"{BASE}/login-page", f"{BASE}/overview"),
    ]
    patterns = [s.get("pattern") for s in _takeover_steps(url_events, [click(2)]) if s.get("pattern")]
    assert patterns == [f"{BASE}/overview**"]


def test_an_automated_login_keeps_the_narrow_bound():
    """The fallback must not loosen the path it was not written for.

    An automated login lands by redirect before any click, so the narrow rule
    finds its landing page and the fallback never runs — the workflow pages the
    human visited afterwards stay out of the check.
    """
    url_events = [
        url(1, "about:blank", f"{BASE}/login-page"),
        url(2, f"{BASE}/login-page", f"{BASE}/overview"),
        url(5, f"{BASE}/overview", f"{BASE}/credit-card"),
    ]
    steps = _takeover_steps(url_events, [click(4)], manual_takeover=False)
    patterns = [s.get("pattern") for s in steps if s.get("pattern")]
    assert patterns and all("credit-card" not in p for p in patterns)
