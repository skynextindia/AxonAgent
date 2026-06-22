"""Notification and alert dispatch module for AxonAI."""

import logging
import requests

logger = logging.getLogger(__name__)


def send_alert(message: str, config: dict):
    """Dispatch an alert message to configured destinations (Telegram, Webhook)."""
    # Telegram alert
    telegram_token = config.get("alert_telegram_token")
    telegram_chat_id = config.get("alert_telegram_chat_id")
    if telegram_token and telegram_chat_id:
        try:
            url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
            payload = {"chat_id": telegram_chat_id, "text": f"🚨 [AxonAI] Alert:\n\n{message}"}
            res = requests.post(url, json=payload, timeout=5)
            if res.status_code == 200:
                logger.info("Alerts: Telegram notification sent.")
            else:
                logger.error("Alerts: Failed to send Telegram alert. Status: %d", res.status_code)
        except Exception as e:
            logger.error("Alerts: Telegram alert error: %s", e)

    # Generic Webhook alert
    webhook_url = config.get("alert_webhook_url")
    if webhook_url:
        try:
            payload = {"content": f"🚨 [AxonAI] Alert:\n{message}"}
            res = requests.post(webhook_url, json=payload, timeout=5)
            if res.status_code in (200, 204):
                logger.info("Alerts: Webhook notification sent.")
            else:
                logger.error("Alerts: Failed to send webhook alert. Status: %d", res.status_code)
        except Exception as e:
            logger.error("Alerts: Webhook alert error: %s", e)

    # Log locally
    logger.info("🚨 [AxonAI Alert] %s", message)
