"""In-memory Telegram alert subscriptions for PacificaEdge."""

from __future__ import annotations

from pydantic import BaseModel


class TelegramAlertSubscription(BaseModel):
    """Telegram alert subscription for a single symbol."""

    symbol: str
    telegram_token: str
    telegram_chat_id: str
    trigger_on: str


telegram_alert_subscriptions: list[TelegramAlertSubscription] = []


def add_or_update_telegram_subscription(sub: TelegramAlertSubscription) -> None:
    """Add a new Telegram alert subscription or replace an existing matching one."""
    global telegram_alert_subscriptions
    for index, existing in enumerate(telegram_alert_subscriptions):
        if (
            existing.symbol == sub.symbol
            and existing.telegram_token == sub.telegram_token
            and existing.telegram_chat_id == sub.telegram_chat_id
        ):
            telegram_alert_subscriptions[index] = sub
            return
    telegram_alert_subscriptions.append(sub)


def get_telegram_subscriptions_for_symbol(symbol: str) -> list[TelegramAlertSubscription]:
    """Return all Telegram alert subscriptions for a symbol."""
    return [subscription for subscription in telegram_alert_subscriptions if subscription.symbol == symbol]
