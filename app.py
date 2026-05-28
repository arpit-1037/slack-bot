"""Flask entrypoint for Slack Events API requests."""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from flask import Flask, jsonify, request
from slack_sdk import WebClient

from claude_solver import AIServiceUnavailableError, solve_task
from database import init_db
from src.slack.slack_handler import SlackEventHandler

load_dotenv()

logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s %(message)s",
)

app = Flask(__name__)
slack = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))
slack_handler = SlackEventHandler(
    slack_client=slack,
    solve_task_func=solve_task,
    ai_unavailable_error=AIServiceUnavailableError,
)


@app.route("/slack/events", methods=["POST"])
def slack_events():
    """Receive Slack events and delegate request handling."""
    response, status = slack_handler.handle_event_payload(request.json or {}, request)
    return jsonify(response), status


@app.route("/health", methods=["GET"])
def health():
    """Health check endpoint."""
    return jsonify({"status": "running", "bot": "slack-claude-bot"})


if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 3000))
    print(f"🚀 Bot running on http://localhost:{port}")
    print(f"📡 Slack events endpoint: http://localhost:{port}/slack/events")
    app.run(host="0.0.0.0", port=port, debug=False)
