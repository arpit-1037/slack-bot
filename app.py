# app.py — main Flask server, receives Slack events and triggers Claude

import os
import hashlib
import hmac
import time
import logging

from flask import Flask, request, jsonify
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError
from dotenv import load_dotenv

from database import init_db, save_task, update_task
from claude_solver import solve_task

# ── Setup ──────────────────────────────────────────────────────────────────────
load_dotenv()

logging.basicConfig(
    filename="bot.log",
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s"
)

app = Flask(__name__)
slack = WebClient(token=os.getenv("SLACK_BOT_TOKEN"))

# Track processed event IDs to prevent duplicate handling
processed_events = set()


# ── Slack Signature Verification ───────────────────────────────────────────────
def verify_slack(req) -> bool:
    """Confirm the request genuinely came from Slack."""
    secret = os.getenv("SLACK_SIGNING_SECRET", "").encode()
    timestamp = req.headers.get("X-Slack-Request-Timestamp", "")
    signature = req.headers.get("X-Slack-Signature", "")

    # Reject requests older than 5 minutes (replay attack guard)
    if not timestamp or abs(time.time() - int(timestamp)) > 300:
        return False

    base = f"v0:{timestamp}:{req.get_data(as_text=True)}"
    expected = "v0=" + hmac.new(secret, base.encode(), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


# ── Slack Helpers ──────────────────────────────────────────────────────────────
def post_message(channel: str, thread_ts: str, text: str):
    """Post a message into a Slack thread."""
    try:
        slack.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)
    except SlackApiError as e:
        logging.error(f"Slack API error: {e.response['error']}")


# ── Main Event Handler ─────────────────────────────────────────────────────────
@app.route("/slack/events", methods=["POST"])
def slack_events():
    data = request.json

    # 1. One-time URL verification handshake with Slack
    if data.get("type") == "url_verification":
        return jsonify({"challenge": data["challenge"]})

    # 2. Verify the request is from Slack
    if not verify_slack(request):
        logging.warning("Unauthorized request rejected.")
        return jsonify({"error": "Unauthorized"}), 403

    event = data.get("event", {})
    event_id = data.get("event_id", "")

    # 3. Deduplicate — Slack sometimes sends the same event twice
    if event_id in processed_events:
        return jsonify({"status": "duplicate"}), 200
    processed_events.add(event_id)

    # 4. Only handle @mention events, ignore messages from bots
    if event.get("type") == "app_mention" and not event.get("bot_id"):
        channel   = event["channel"]
        thread_ts = event.get("thread_ts") or event["ts"]
        user      = event["user"]
        task_text = event.get("text", "")

        logging.info(f"Task from @{user} in {channel}: {task_text}")

        # 5. Acknowledge immediately (Slack needs a reply within 3 seconds)
        post_message(channel, thread_ts, f"⏳ <@{user}> On it! Solving your task...")

        # 6. Save task to database
        task_id = save_task(slack_user=user, channel=channel, task_text=task_text, thread_ts=thread_ts)

        # 7. Send to Claude
        try:
            solution = solve_task(
                task_text,
                thread_ts=thread_ts,
                channel=channel,
                slack_user=user,
            )
            status   = "solved"
            logging.info(f"Task {task_id} solved successfully.")
        except Exception as e:
            solution = f"❌ Sorry, I ran into an error: {str(e)}"
            status   = "error"
            logging.error(f"Task {task_id} failed: {e}")

        # 8. Store solution in database
        update_task(task_id, solution, status)

        # 9. Post Claude's solution back to Slack
        post_message(channel, thread_ts, f"✅ *Solution:*\n\n{solution}")

    return jsonify({"status": "ok"})


# ── Health Check ───────────────────────────────────────────────────────────────
@app.route("/health", methods=["GET"])
def health():
    return jsonify({"status": "running", "bot": "slack-claude-bot"})


# ── Start ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    init_db()
    port = int(os.getenv("PORT", 3000))
    print(f"🚀 Bot running on http://localhost:{port}")
    print(f"📡 Slack events endpoint: http://localhost:{port}/slack/events")
    app.run(host="0.0.0.0", port=port, debug=False)
