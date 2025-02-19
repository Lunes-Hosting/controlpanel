
import datetime
import logging

import requests

import config


class WebhookLogger():
    STATUS_MAP = {
        -1: {"color": 0x95A5A6, "title": "No Code"},   # Gray
        0: {"color": 0x3498DB, "title": "Info"},      # Blue
        1: {"color": 0xF1C40F, "title": "Warning"}, # Yellow
        2: {"color": 0xE74C3C, "title": "Error"},    # Red
    }

    logger = logging.getLogger(__name__)

    def webhook_log(self, message: str, status: int = -1):
        """
        Sends a log message to a Discord webhook with formatting.

        Args:
            message: Message to send.
            status: Status of Message (-1: Debug, 0: Info, 1: Warning, 2: Error).

        Returns:
            None
        """

        status_info = self.STATUS_MAP.get(status, self.STATUS_MAP[-1])
        # Log locally
        
        self.logger.info(message)

        # Create Discord embed
        embed = {
            "title": f"**{status_info['title']} Log**",
            "description": message,
            "color": status_info["color"],
            "footer": {"text": f"Logged at {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}"}
        }

        payload = {
            "username": "Webhook Logger",
            "embeds": [embed]
        }

        # Send to Discord webhook
        try:
            resp = requests.post(config.WEBHOOK_URL, json=payload)
            resp.raise_for_status()
        except requests.RequestException as e:
            self.logger.error(f"Failed to send webhook log: {e}")