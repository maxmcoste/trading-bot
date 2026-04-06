"""Email notifier for trade alerts and errors."""

from __future__ import annotations

import logging
import smtplib
from email.mime.text import MIMEText

from config import settings

logger = logging.getLogger(__name__)


class Notifier:
    def __init__(self):
        self.email = settings.NOTIFY_EMAIL
        self.on_trade = settings.NOTIFY_ON_TRADE
        self.on_error = settings.NOTIFY_ON_ERROR
        self.on_daily = settings.NOTIFY_ON_DAILY_SUMMARY

    def _send_email(self, subject: str, body: str):
        if not self.email:
            return
        try:
            msg = MIMEText(body, "plain")
            msg["Subject"] = f"[TradingBot] {subject}"
            msg["From"] = self.email
            msg["To"] = self.email

            with smtplib.SMTP("localhost", 25, timeout=10) as smtp:
                smtp.send_message(msg)

            logger.info(f"Notification sent: {subject}")
        except Exception as e:
            logger.warning(f"Failed to send notification: {e}")

    def notify_trade(self, symbol: str, action: str, confidence: float,
                     price: float, reasoning: str):
        if not self.on_trade:
            return
        body = (
            f"Symbol: {symbol}\n"
            f"Action: {action}\n"
            f"Confidence: {confidence:.2f}\n"
            f"Price: ${price:.2f}\n"
            f"Reasoning: {reasoning}\n"
        )
        self._send_email(f"{action} {symbol} (conf: {confidence:.0%})", body)

    def notify_error(self, component: str, error: str):
        if not self.on_error:
            return
        self._send_email(f"ERROR in {component}", f"Component: {component}\nError: {error}")

    def notify_daily_summary(self, stats: dict):
        if not self.on_daily:
            return
        body = "\n".join(f"{k}: {v}" for k, v in stats.items())
        self._send_email("Daily Summary", body)

    def send_test(self) -> bool:
        try:
            self._send_email("Test Notification", "This is a test email from TradingBot.")
            return True
        except Exception:
            return False


if __name__ == "__main__":
    n = Notifier()
    print(f"Notifier configured. Email: {n.email or '(not set)'}")
    print(f"Notify on: trade={n.on_trade}, error={n.on_error}, daily={n.on_daily}")
