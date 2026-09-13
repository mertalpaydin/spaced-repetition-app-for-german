"""Phase 3a: the JSON API over the phase 2 engine, and the static page."""

import json
import threading
import urllib.request
from datetime import timedelta
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from src.cli.serve import WEB_DIR, Api, ApiError, make_handler
from src.engine.review_log import ReviewLog
from src.engine.session import Settings

from tests.test_phase2_engine import T0, _deck


def _api(tmp_path: Path) -> Api:
    clock = [T0]
    return Api(
        _deck(), ReviewLog(tmp_path / "log.jsonl", now=lambda: clock[0]), now=lambda: clock[0]
    )


def test_next_answer_and_mark_round_trip(tmp_path: Path) -> None:
    api = _api(tmp_path)
    nxt = api.next()
    assert not nxt["done"] and nxt["unit"]["unit_id"] == "vp:warten_auf" and nxt["new"]
    assert nxt["card"]["gaps"] == [{"start": 3, "end": 9}, {"start": 10, "end": 13}]
    result = api.answer(
        {"card_id": nxt["card"]["card_id"], "typed": ["wartet", "auf"], "elapsed_ms": 900}
    )
    assert result["rating"] == "good" and result["marked"] == "Er [wartet] [auf] den Bus."
    assert result["unit"]["display"] == "warten auf +Akk"
    with pytest.raises(ApiError):
        api.answer({"card_id": nxt["card"]["card_id"], "typed": ["x"]})
    with pytest.raises(ApiError):
        api.answer({"card_id": "nope", "typed": []})
    assert api.mark({"unit_id": "cn:trotzdem", "known": True, "source": "practice"}) == {"ok": True}
    stats = api.stats()
    assert stats["known"] == 1 and stats["reviews_total"] == 1 and stats["young"] == 1
    assert nxt["today"] == {"done": 0, "target": 40, "left": 40, "due": 0}
    assert api.units()["young"][0]["unit_id"] == "vp:warten_auf"
    assert api.history()["items"][0]["rating"] == "good"
    tri = api.triage(10)
    # warten reviewed, trotzdem marked, und trivial; triage judges units, glossed or not
    assert [u["unit_id"] for u in tri["units"]] == ["sv:aufstehen"]


def test_http_serves_the_page_and_the_api(tmp_path: Path) -> None:
    api = _api(tmp_path)
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(api, WEB_DIR))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urllib.request.urlopen(base + "/") as res:
            assert res.status == 200 and b"<title>Phrasen</title>" in res.read()
        with urllib.request.urlopen(base + "/api/next") as res:
            data = json.loads(res.read())
        assert data["card"]["sentence"] == "Er wartet auf den Bus."
        req = urllib.request.Request(
            base + "/api/answer",
            data=json.dumps({"card_id": data["card"]["card_id"], "typed": [None, "auf"]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req) as res:
            assert json.loads(res.read())["outcome"] == "revealed"
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(base + "/../pyproject.toml")
        assert exc.value.code == 404
    finally:
        server.shutdown()
        server.server_close()


def test_deck_info_and_stats_after_a_day(tmp_path: Path) -> None:
    api = _api(tmp_path)
    assert api.deck_info()["deck_version"] == "test"
    api.now = lambda: T0 + timedelta(days=1)
    assert api.stats()["streak_days"] == 0


def test_spent_budget_stops_before_due_cards_unless_mid_learning_step(tmp_path: Path) -> None:
    api = _api(tmp_path)
    api.settings = Settings(cards_per_day=1)
    first = api.next()
    api.answer({"card_id": first["card"]["card_id"], "typed": ["x", "auf"], "elapsed_ms": 1})
    # budget spent; warten's learning step is due in 10 minutes: mid-step, so it is served
    api.now = lambda: T0 + timedelta(minutes=11)
    nxt = api.next()
    assert not nxt["limit_reached"] and nxt["unit"]["unit_id"] == "vp:warten_auf"
    api.answer({"card_id": nxt["card"]["card_id"], "typed": ["x", "auf"], "elapsed_ms": 1})
    # a new unit is not mid-step: the limit panel comes first, over_limit serves it
    api.now = lambda: T0 + timedelta(minutes=12)
    api.log.record_mark(unit_id="vp:warten_auf", known=True, source="defer")
    assert api.next()["limit_reached"]
    assert api.next(over_limit=True)["unit"]["unit_id"] == "cn:trotzdem"
    assert api.units()["deferred"][0]["unit_id"] == "vp:warten_auf"
