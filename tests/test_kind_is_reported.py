"""The compiled KIND is always stated, and confirmed when nobody chose it.

Reporting spoke only when browser mode was auto-selected, so choosing REPLAY was
silent — an ICICI capture asked for as a browser skill compiled to two Finacle
POSTs with nothing anywhere saying a decision had been taken. A default is fine;
a default nobody is told about is not.
"""

import capture_import as ci


def _run(capsys, det, declared=False, skill_id="icici-bank-cc-statement"):
    ci._report_kind({"browser_detection": det}, {"skill_id": skill_id}, declared=declared)
    return capsys.readouterr().err


def test_replay_says_so_instead_of_saying_nothing(capsys):
    out = _run(capsys, {"unreplayable": False, "reasons": []})
    assert "KIND: replay" in out
    assert "fire the recorded requests rather than drive the page" in out


def test_an_undetected_kind_asks_for_confirmation(capsys):
    out = _run(capsys, {"unreplayable": False})
    assert "CONFIRM THE KIND WITH THE MEMBER BEFORE INSTALLING" in out
    assert "--browser-driven" in out


def test_a_declared_kind_is_not_second_guessed(capsys):
    # The member already said; asking again is noise.
    out = _run(capsys, {"unreplayable": False}, declared=True)
    assert "KIND: browser-driven" in out
    assert "you asked for it" in out
    assert "CONFIRM THE KIND" not in out


def test_auto_detected_browser_mode_still_explains_itself(capsys):
    out = _run(capsys, {"unreplayable": True, "reasons": ["opaque {data,key} bodies"]})
    assert "KIND: browser-driven" in out
    assert "encrypts its requests" in out
    # Still unconfirmed by a human, so still worth a check.
    assert "CONFIRM THE KIND" in out


def test_the_replay_reasons_are_shown_when_there_are_any(capsys):
    out = _run(capsys, {"unreplayable": False, "reasons": ["bodies are plain JSON"]})
    assert "bodies are plain JSON" in out
