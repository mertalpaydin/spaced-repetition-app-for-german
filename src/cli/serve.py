"""The local web client: the phase 2 engine behind a small JSON API.

    uv run python -m src.cli.serve      # prints the laptop URL and the LAN URL for the phone

Phase 3a. The browser page in ``web/`` talks to this server; grading,
scheduling and the review log stay in Python, so the laptop and the phone
(on the same network) share one log. The offline PWA with the engine in
JavaScript is phase 3b. No dependency beyond the standard library.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import socket
import sys
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from src.contracts import PhraseCard, PhraseUnit
from src.engine.fsrs import FSRSEngine
from src.engine.grading import grade_card, render_marked
from src.engine.review_log import DEFAULT_LOG_PATH, ReviewLog, derive_state
from src.engine.session import Deck, Settings, next_unit, pick_card, untriaged_units
from src.engine.stats import compute_stats
from src.phrases.export import DEFAULT_DECK_DIR

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


class ApiError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


def _unit_payload(unit: PhraseUnit) -> dict[str, Any]:
    return {
        "unit_id": unit.unit_id,
        "kind": unit.kind,
        "display": unit.display_de + (f" +{unit.case}" if unit.case else ""),
        "rank": unit.rank,
        "cefr": unit.cefr,
        "gloss": unit.gloss_en,
    }


def _card_payload(card: PhraseCard) -> dict[str, Any]:
    return {
        "card_id": card.card_id,
        "sentence": card.sentence_de,
        "gaps": [{"start": g.start, "end": g.end} for g in card.gaps],
        "gloss": card.gloss_en,
        "context_de": card.context_de,
        "context_en": card.context_en,
    }


class Api:
    """Every endpoint as a method, so tests call them without HTTP. One lock:
    the log is append-only and the state is replayed per request."""

    def __init__(
        self,
        deck: Deck,
        log: ReviewLog,
        *,
        settings: Settings | None = None,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.deck = deck
        self.log = log
        self.settings = settings or Settings()
        self.engine = FSRSEngine(request_retention=self.settings.retention)
        self.now = now
        self.lock = threading.Lock()
        self._cards = {c.card_id: c for cards in deck.cards_by_unit.values() for c in cards}

    def deck_info(self) -> dict[str, Any]:
        m = self.deck.manifest
        return {
            "deck_version": m.deck_version,
            "units": m.unit_count,
            "cards": m.card_count,
            "glossed_cards": m.glossed_card_count,
        }

    def next(self) -> dict[str, Any]:
        with self.lock:
            state = derive_state(self.log.entries, self.engine)
            unit = next_unit(self.deck, state, self.engine, self.settings, self.now())
            if unit is None:
                return {"done": True}
            card = pick_card(self.deck, unit, state)
            if card is None:
                return {"done": True}
            is_new = unit.unit_id not in state.records
            return {
                "done": False,
                "unit": _unit_payload(unit),
                "card": _card_payload(card),
                "new": is_new,
            }

    def answer(self, body: dict[str, Any]) -> dict[str, Any]:
        card = self._cards.get(str(body.get("card_id", "")))
        if card is None:
            raise ApiError(HTTPStatus.NOT_FOUND, "unknown card")
        typed_raw = body.get("typed")
        if not isinstance(typed_raw, list) or len(typed_raw) != len(card.gaps):
            raise ApiError(HTTPStatus.BAD_REQUEST, f"typed must have {len(card.gaps)} entries")
        typed = [None if t is None else str(t) for t in typed_raw]
        elapsed = int(body.get("elapsed_ms", 0) or 0)
        grade = grade_card(card, typed)
        unit = self.deck.by_id[card.unit_id]
        with self.lock:
            self.log.record_review(
                unit_id=card.unit_id,
                card_id=card.card_id,
                rating=grade.rating,
                outcome=grade.outcome,
                answers=[g.typed for g in grade.gaps],
                expected=[g.expected for g in grade.gaps],
                elapsed_ms=max(elapsed, 0),
                deck_version=self.deck.manifest.deck_version,
            )
        return {
            "rating": grade.rating,
            "outcome": grade.outcome,
            "gaps": [
                {"typed": g.typed, "expected": g.expected, "outcome": g.outcome, "ok": g.accepted}
                for g in grade.gaps
            ],
            "marked": render_marked(card),
            "unit": _unit_payload(unit),
        }

    def mark(self, body: dict[str, Any]) -> dict[str, Any]:
        unit_id = str(body.get("unit_id", ""))
        if unit_id not in self.deck.by_id:
            raise ApiError(HTTPStatus.NOT_FOUND, "unknown unit")
        source = "triage" if body.get("source") == "triage" else "practice"
        with self.lock:
            self.log.record_mark(
                unit_id=unit_id, known=bool(body.get("known", True)), source=source
            )
        return {"ok": True}

    def triage(self, batch: int) -> dict[str, Any]:
        with self.lock:
            state = derive_state(self.log.entries, self.engine)
            units = untriaged_units(self.deck, state)[: max(1, min(batch, 200))]
        return {"units": [_unit_payload(u) for u in units], "remaining": len(units)}

    def stats(self) -> dict[str, Any]:
        with self.lock:
            state = derive_state(self.log.entries, self.engine)
            s = compute_stats(self.deck, state, self.log.entries, self.engine, self.now())
        return {
            "known": s.known,
            "learning": s.learning,
            "young": s.young,
            "mature": s.mature,
            "due_now": s.due_now,
            "new_remaining": s.new_remaining,
            "reviews_today": s.reviews_today,
            "reviews_total": s.reviews_total,
            "streak_days": s.streak_days,
            "retention_30d": s.retention_30d,
            "coverage": [
                {"band": band, "seen": seen, "total": total}
                for band, (seen, total) in sorted(s.coverage.items())
            ],
        }


def make_handler(api: Api, web_dir: Path) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _static(self, path: str) -> None:
            name = "index.html" if path in {"", "/"} else path.lstrip("/")
            target = (web_dir / name).resolve()
            if not str(target).startswith(str(web_dir.resolve())) or not target.is_file():
                self._json(HTTPStatus.NOT_FOUND, {"error": "not found"})
                return
            data = target.read_bytes()
            ctype = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
            if ctype.startswith("text/") or ctype in {"application/javascript", "application/json"}:
                ctype += "; charset=utf-8"
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def _body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8") or "{}")
            except ValueError as exc:
                raise ApiError(HTTPStatus.BAD_REQUEST, "invalid JSON") from exc
            return body if isinstance(body, dict) else {}

        def do_GET(self) -> None:  # noqa: N802
            path, _, query = self.path.partition("?")
            try:
                if path == "/api/next":
                    self._json(HTTPStatus.OK, api.next())
                elif path == "/api/stats":
                    self._json(HTTPStatus.OK, api.stats())
                elif path == "/api/deck":
                    self._json(HTTPStatus.OK, api.deck_info())
                elif path == "/api/triage":
                    batch = 50
                    for part in query.split("&"):
                        if part.startswith("batch="):
                            batch = int(part[6:] or 50)
                    self._json(HTTPStatus.OK, api.triage(batch))
                elif path.startswith("/api/"):
                    self._json(HTTPStatus.NOT_FOUND, {"error": "no such endpoint"})
                else:
                    self._static(path)
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})

        def do_POST(self) -> None:  # noqa: N802
            try:
                body = self._body()
                if self.path == "/api/answer":
                    self._json(HTTPStatus.OK, api.answer(body))
                elif self.path == "/api/mark":
                    self._json(HTTPStatus.OK, api.mark(body))
                else:
                    self._json(HTTPStatus.NOT_FOUND, {"error": "no such endpoint"})
            except ApiError as exc:
                self._json(exc.status, {"error": str(exc)})

    return Handler


def lan_address() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deck", type=Path, default=DEFAULT_DECK_DIR)
    parser.add_argument("--log", type=Path, default=DEFAULT_LOG_PATH)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--host", default="0.0.0.0", help="0.0.0.0 lets the phone on the LAN connect"
    )
    parser.add_argument("--new-per-day", type=int, default=10)
    args = parser.parse_args(argv)
    api = Api(
        Deck.load(args.deck), ReviewLog(args.log), settings=Settings(new_per_day=args.new_per_day)
    )
    server = ThreadingHTTPServer((args.host, args.port), make_handler(api, WEB_DIR))
    print(
        f"laptop: http://localhost:{args.port}   phone (same Wi-Fi): http://{lan_address()}:{args.port}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
