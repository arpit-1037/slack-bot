"""Slack response formatting and posting helpers."""

from __future__ import annotations

from typing import Any

from slack_sdk.errors import SlackApiError

from src.utils.helpers import get_logger, int_env

log = get_logger(__name__)

def slack_message_chunk_size() -> int:
    """Return the configured Slack message chunk size."""
    return int_env("SLACK_MESSAGE_CHUNK_SIZE", 39000, 1000, 39000)


def split_message(text: str, chunk_size: int | None = None) -> list[str]:
    """Split long Slack replies without dropping any content."""
    chunk_size = chunk_size or slack_message_chunk_size()
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    remaining = text
    while len(remaining) > chunk_size:
        split_at = remaining.rfind("\n", 0, chunk_size)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, chunk_size)
        if split_at <= 0:
            split_at = chunk_size
        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]

    if remaining:
        chunks.append(remaining)
    return chunks


def post_message(client: Any, channel: str, thread_ts: str, text: str) -> None:
    """Post a message into a Slack thread, chunking long responses."""
    chunks = split_message(text)
    total = len(chunks)
    for index, chunk in enumerate(chunks, start=1):
        if total > 1:
            chunk = f"{chunk}\n\n_Continued {index}/{total}_"
        try:
            client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=chunk)
        except SlackApiError as error:
            log.error("Slack API error: %s", error.response["error"])
            break
