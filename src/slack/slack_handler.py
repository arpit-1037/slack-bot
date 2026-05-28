"""Slack event handling and request lifecycle tracing."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from typing import Any, Callable

from database import save_task, update_task
from src.slack.slack_response import post_message
from src.utils.helpers import get_logger, new_request_id

log = get_logger(__name__)


class SlackEventHandler:
    """Handle Slack event payloads while preserving thread reply behavior."""

    def __init__(
        self,
        slack_client: Any,
        solve_task_func: Callable[..., str],
        ai_unavailable_error: type[Exception],
    ) -> None:
        self.slack_client = slack_client
        self.solve_task_func = solve_task_func
        self.ai_unavailable_error = ai_unavailable_error
        self.processed_events: set[str] = set()

    def verify_slack(self, req: Any) -> bool:
        """Confirm the request genuinely came from Slack."""
        secret = os.getenv("SLACK_SIGNING_SECRET", "").encode()
        timestamp = req.headers.get("X-Slack-Request-Timestamp", "")
        signature = req.headers.get("X-Slack-Signature", "")

        try:
            request_time = int(timestamp)
        except (TypeError, ValueError):
            return False

        if not timestamp or abs(time.time() - request_time) > 300:
            return False

        base = f"v0:{timestamp}:{req.get_data(as_text=True)}"
        expected = "v0=" + hmac.new(secret, base.encode(), hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def handle_event_payload(self, data: dict, req: Any) -> tuple[dict, int]:
        """Handle a Slack Events API payload and return a Flask-friendly response."""
        if data.get("type") == "url_verification":
            return {"challenge": data["challenge"]}, 200

        if not self.verify_slack(req):
            log.warning("Unauthorized Slack request rejected.")
            return {"error": "Unauthorized"}, 403

        event = data.get("event", {})
        event_id = data.get("event_id", "")
        request_id = event_id or new_request_id()

        if event_id in self.processed_events:
            log.info("request_id=%s duplicate Slack event skipped", request_id)
            return {"status": "duplicate"}, 200
        self.processed_events.add(event_id)

        if event.get("type") == "app_mention" and not event.get("bot_id"):
            self._handle_app_mention(event, request_id)

        return {"status": "ok"}, 200

    def _handle_app_mention(self, event: dict, request_id: str) -> None:
        """Process one Slack app mention event."""
        channel = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]
        user = event["user"]
        task_text = event.get("text", "")

        log.info(
            "request_id=%s task received user=%s channel=%s thread_ts=%s text=%s",
            request_id, user, channel, thread_ts, task_text,
        )

        post_message(self.slack_client, channel, thread_ts, f"⏳ <@{user}> On it! Solving your task...")

        task_id = save_task(
            slack_user=user,
            channel=channel,
            task_text=task_text,
            thread_ts=thread_ts,
        )

        message_heading = "✅ *Solution:*"
        try:
            solution = self.solve_task_func(
                task_text,
                thread_ts=thread_ts,
                channel=channel,
                slack_user=user,
                request_id=request_id,
            )
            status = "solved"
            log.info("request_id=%s task_id=%s solved successfully", request_id, task_id)
        except self.ai_unavailable_error as error:
            solution = f"Sorry, all AI services are temporarily unavailable. {str(error)}"
            status = "error"
            message_heading = "⚠️ *AI unavailable:*"
            log.error(
                "request_id=%s task_id=%s failed because AI providers were unavailable: %s",
                request_id, task_id, error,
            )
        except Exception as error:
            solution = f"Sorry, I ran into an error: {str(error)}"
            status = "error"
            message_heading = "❌ *Error:*"
            log.error("request_id=%s task_id=%s failed: %s", request_id, task_id, error)

        update_task(task_id, solution, status)
        post_message(self.slack_client, channel, thread_ts, f"{message_heading}\n\n{solution}")
