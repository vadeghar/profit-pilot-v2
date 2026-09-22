"""Telegram notification dispatcher with rate limiting and coalescing"""

import time
import requests
from typing import Optional
from utils import Logger


class TelegramNotifier:
    """Telegram alert dispatcher conforming to Architecture v2"""

    def __init__(self, bot_token: str, chat_id: str, enabled: bool = True):
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled and bool(bot_token and chat_id)
        self.logger = Logger("notifier.telegram")
        self._last_sent_time = 0.0
        self._rate_limit_delay = 1.0  # max 1 msg/sec

    def send_message(self, message: str, parse_mode: str = "Markdown") -> bool:
        """Send an urgent notification to Telegram"""
        if not self.enabled:
            return False

        try:
            # Respect rate limit
            now = time.time()
            if now - self._last_sent_time < self._rate_limit_delay:
                time.sleep(self._rate_limit_delay - (now - self._last_sent_time))

            url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": parse_mode
            }
            res = requests.post(url, json=payload, timeout=5)
            self._last_sent_time = time.time()
            if res.status_code == 200 and res.json().get('ok'):
                self.logger.info("Telegram notification delivered successfully")
                return True
            else:
                self.logger.warning(f"Telegram send failed: {res.text}")
                return False
        except Exception as e:
            self.logger.error(f"Telegram dispatcher error: {e}")
            return False
