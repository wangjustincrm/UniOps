"""Microsoft Teams notification via Incoming Webhook (Adaptive Card)."""
import logging

import httpx

logger = logging.getLogger(__name__)


async def send_teams_card(
    webhook_url: str,
    title: str,
    body: str,
    link: str | None = None,
    link_label: str = "View in EPMS",
) -> None:
    """
    Post an Adaptive Card to a Teams Incoming Webhook.

    Raises on non-2xx response or network error.
    """
    actions = []
    if link:
        actions.append({
            "type": "Action.OpenUrl",
            "title": link_label,
            "url": link,
        })

    card: dict = {
        "type": "message",
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.card.adaptive",
                "content": {
                    "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
                    "type": "AdaptiveCard",
                    "version": "1.4",
                    "body": [
                        {
                            "type": "TextBlock",
                            "text": title,
                            "weight": "Bolder",
                            "size": "Medium",
                            "wrap": True,
                        },
                        {
                            "type": "TextBlock",
                            "text": body,
                            "wrap": True,
                            "spacing": "Small",
                        },
                    ],
                    "actions": actions,
                },
            }
        ],
    }

    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(webhook_url, json=card)
        resp.raise_for_status()
