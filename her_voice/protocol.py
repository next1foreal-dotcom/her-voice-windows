"""Stateful guards around upstream's unscoped response.cancel implementation."""

from __future__ import annotations


ALLOWED_CLIENT_EVENTS = {
    "session.update", "input_audio_buffer.append", "input_audio_buffer.commit",
    "conversation.item.create", "conversation.item.delete", "response.create",
    "response.cancel",
}


class CancelGuard:
    """An old response ID must never cancel a newer response in the engine."""

    def __init__(self) -> None:
        self.active_response_id: str | None = None
        self.seen: set[str] = set()

    def observe(self, event: dict) -> None:
        kind = event.get("type")
        response = event.get("response", {})
        if not isinstance(response, dict):
            return
        if kind == "response.created":
            response_id = response.get("id")
            if isinstance(response_id, str) and response_id not in self.seen:
                self.active_response_id = response_id
                self.seen.add(response_id)
        elif kind == "response.done" and response.get("id") == self.active_response_id:
            self.active_response_id = None

    def allow(self, event: dict) -> bool:
        kind = event.get("type")
        if kind not in ALLOWED_CLIENT_EVENTS:
            return False
        if kind == "response.cancel":
            # Upstream ignores event.response_id. This bridge enforces it.
            return self.active_response_id is not None and event.get("response_id") == self.active_response_id
        return True
