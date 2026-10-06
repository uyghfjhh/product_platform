"""Opt-in webhook delivery with stable idempotency keys and bounded retries."""

import json
import os
import urllib.request
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field, HttpUrl


class WebhookInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    url: HttpUrl
    credential_env: str = Field(default="", pattern=r"^$|^[A-Za-z_][A-Za-z0-9_]*$")
    enabled: bool = False
    acknowledge_send: bool = False


def send(webhook, event):
    headers = {"Content-Type": "application/json", "Idempotency-Key": event["id"]}
    if webhook["credential_env"]:
        secret = os.environ.get(webhook["credential_env"])
        if not secret:
            raise ValueError("通知凭据未配置")
        headers["Authorization"] = "Bearer " + secret
    payload = {
        key: event[key]
        for key in (
            "id",
            "task_id",
            "environment_id",
            "action",
            "target",
            "status",
            "created_at",
        )
    }
    request = urllib.request.Request(
        webhook["url"], data=json.dumps(payload).encode(), headers=headers
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        if not 200 <= response.status < 300:
            raise ValueError("通知接收方未确认")


def deliver(repository, sender=send):
    now = datetime.now(UTC)
    for event in repository.list("notifications")[:200]:
        for hook in repository.list("webhooks"):
            if not hook["enabled"]:
                continue
            previous = (event.get("deliveries") or {}).get(hook["id"], {})
            if previous.get("status") == "SENT" or previous.get("attempts", 0) >= 10:
                continue
            if (
                previous.get("next_attempt_at")
                and datetime.fromisoformat(previous["next_attempt_at"]) > now
            ):
                continue
            attempt = previous.get("attempts", 0) + 1
            result = {"attempts": attempt, "last_attempt_at": now.isoformat()}
            try:
                sender(hook, event)
                result["status"] = "SENT"
            except (OSError, ValueError):
                result.update(
                    status="RETRY",
                    next_attempt_at=(
                        now + timedelta(seconds=min(300, 2**attempt))
                    ).isoformat(),
                )
            event.setdefault("deliveries", {})[hook["id"]] = result
            repository.put("notifications", event, event["id"])
            return  # One bounded network request per control-loop tick.
