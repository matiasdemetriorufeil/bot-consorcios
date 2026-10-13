"""panel.js's two decisions without a page (it exposes them for this): when a poll's answer
means the session is gone (and whether to reload or warn), and which alerts are new for this
browser (shared by its tabs). Runs the script with Node; skipped where there is none (the
container), never anywhere else."""

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "app" / "admin" / "static" / "panel.js"
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="Node is not installed here")


def _run(call: str) -> Any:
    """Loads panel.js without a page (it stops before touching one) and prints call's result."""
    program = (
        "globalThis.window = globalThis;\n"
        f"require({json.dumps(str(SCRIPT))});\n"
        f"process.stdout.write(JSON.stringify({call}));\n"
    )
    done = subprocess.run(  # noqa: S603 (node and our own script, no outside input)
        [NODE or "node", "-e", program], capture_output=True, text=True, timeout=30, check=True
    )
    return json.loads(done.stdout)


LOGIN = "/admin/login"
NUMBERS = "https://x.test/admin/pending/numbers"


@pytest.mark.parametrize(
    ("status", "url", "is_json", "draft", "login", "expected"),
    [
        (200, NUMBERS, True, False, LOGIN, "ok"),
        # A server error, an odd answer, a network cut (status 0): the next poll, no reload.
        (500, NUMBERS, False, False, LOGIN, "retry"),
        (502, NUMBERS, False, True, LOGIN, "retry"),
        (0, "", False, False, LOGIN, "retry"),
        (200, NUMBERS, False, False, LOGIN, "retry"),
        # The session is gone: the login.
        (401, NUMBERS, False, False, LOGIN, "reload"),
        (403, NUMBERS, False, False, LOGIN, "reload"),
        (200, "https://x.test/admin/login?next=x", False, False, LOGIN, "reload"),
        # The page gives the login as a whole URL (url_for): only the path counts.
        (200, "https://x.test/admin/login", False, False, "https://x.test/admin/login", "reload"),
        # ... with a message being written: a notice instead of losing it.
        (200, "https://x.test/admin/login", False, True, LOGIN, "warn"),
        (401, "https://x.test/admin/conversations/poll", False, True, LOGIN, "warn"),
    ],
)
def test_when_the_session_is_gone(
    status: int, url: str, is_json: bool, draft: bool, login: str, expected: str
) -> None:
    args = json.dumps([status, url, is_json, draft, login])[1:-1]
    assert _run(f"window.panelSessionAction({args})") == expected


DAY = 24 * 3600 * 1000
KEEP = 3 * DAY


def _claim(stored: Any, keys: list[str], now: int) -> dict[str, Any]:
    args = json.dumps([stored, keys, now, KEEP])[1:-1]
    return _run(f"window.panelClaimAlerts({args})")


def test_the_first_time_everything_is_taken_as_seen() -> None:
    result = _claim(None, ["conv:1:100", "claim:7:urgent"], 1000)
    assert result == {"fresh": [], "stored": {"conv:1:100": 1000, "claim:7:urgent": 1000}}


def test_an_alert_sounds_once_across_pages_and_tabs() -> None:
    # Another tab (or the page before) already alerted conv:1; conv:2 is new.
    stored = {"conv:1:100": 1000}
    first = _claim(stored, ["conv:1:100", "conv:2:200"], 2000)
    assert first["fresh"] == ["conv:2:200"]
    # The next tab reads what this one wrote: nothing new.
    again = _claim(first["stored"], ["conv:1:100", "conv:2:200"], 3000)
    assert again["fresh"] == []


def test_handed_off_again_alerts_again() -> None:
    result = _claim({"conv:1:100": 1000}, ["conv:1:500"], 2000)
    assert result["fresh"] == ["conv:1:500"]


def test_old_keys_that_no_longer_wait_are_dropped() -> None:
    now = 10 * DAY
    stored = {"conv:1:1": now - 4 * DAY, "conv:2:2": now - DAY, "conv:3:3": now - 5 * DAY}
    result = _claim(stored, ["conv:3:3"], now)
    # Old and gone: dropped. Recent: kept (so it does not alert again if it comes back). Still
    # waiting: kept, however old.
    assert result["stored"] == {"conv:2:2": now - DAY, "conv:3:3": now - 5 * DAY}
    assert result["fresh"] == []


@pytest.mark.parametrize(
    ("shown", "new", "busy", "expected"),
    [
        ("100:5", "100:5", False, "none"),  # no claim changed
        ("100:5", "", False, "none"),
        ("100:5", "100:6", False, "refresh"),  # one changed: the list is redrawn
        # ... but not while she types in the search or has the filters open: later.
        ("100:5", "100:6", True, "wait"),
        ("100:5", "100:5", True, "none"),
    ],
)
def test_the_claims_list_redraws_only_when_free(
    shown: str, new: str, busy: bool, expected: str
) -> None:
    args = json.dumps([shown, new, busy])[1:-1]
    assert _run(f"window.panelClaimsListAction({args})") == expected


# Fake timers: the calls of a debounced function while she types, then the time going by.
DEBOUNCE = """(function () {
  var timers = {}, next = 1, now = 0, calls = [];
  function setT(fn, ms) { var id = next++; timers[id] = { at: now + ms, fn: fn }; return id; }
  function clearT(id) { delete timers[id]; }
  function advance(ms) {
    now += ms;
    Object.keys(timers).forEach(function (id) {
      if (timers[id] && timers[id].at <= now) { var t = timers[id]; delete timers[id]; t.fn(); }
    });
  }
  var search = window.panelDebounce(function (q) { calls.push(q); }, 350, setT, clearT);
  search("a"); advance(100); search("an"); advance(100); search("ana");
  var before = calls.slice();
  advance(349); var almost = calls.slice();
  advance(1);
  return { before: before, almost: almost, after: calls };
})()"""


def test_the_search_waits_until_she_stops_typing() -> None:
    result = _run(DEBOUNCE)
    # Three keys, one request: with the last text, 350 ms after the last key.
    assert result == {"before": [], "almost": [], "after": ["ana"]}
