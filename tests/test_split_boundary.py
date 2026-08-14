def test_an_empty_workflow_half_is_refused_not_returned():
    """A boundary that places nothing throws the recording away silently.

    `_keep` sends anything it cannot PLACE to the login side, deliberately, so a
    login request never leaks into the workflow. A boundary carrying a null
    position places nothing — every event goes left, the workflow half comes out
    empty, and the compiler is handed a capture of a journey that was never
    sliced off. It compiled to nothing and said nothing, and the agent reading
    that concluded the RECORDING was wrong: it asked a member to sign in and
    drive the whole journey again to route around a bug that had already
    discarded their capture.
    """
    import pytest
    from noui_core.capture import split as split_mod

    bundle = {
        "click_events": [
            {"seq": 1, "url": "https://x.test/login"},
            {"seq": 4, "url": "https://x.test/app"},
        ],
        "url_events": [
            {"seq": 2, "from_url": "https://x.test/login", "to_url": "https://x.test/app"}
        ],
    }
    # A boundary that can place nothing: every event lands on the login side.
    monkey = lambda _b: {"seq": None, "timestamp": None}  # noqa: E731
    original = split_mod._boundary_event
    split_mod._boundary_event = monkey
    try:
        with pytest.raises(split_mod.SplitError) as caught:
            split_mod.split_bundle(bundle)
    finally:
        split_mod._boundary_event = original

    assert "empty workflow half" in str(caught.value)
    # Says whose fault it is, because the last time this happened the human was
    # asked to re-record something that was never broken.
    assert "the recording itself is intact" in str(caught.value).lower()


def test_no_login_segment_still_returns_none():
    """Distinct from the failure above: 'nothing to split' is an ordinary answer."""
    from noui_core.capture import split as split_mod

    assert split_mod.split_bundle({"click_events": [], "url_events": []}) is None
