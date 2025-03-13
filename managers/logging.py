"""
Logging Module
=================

This module handles all logging operations including:
- Webhook logging to Discord
- Status code mapping for log messages
- Formatted log messages

Functions in this module provide consistent logging across the application.
"""

import requests
import json
import datetime
import threading
from config import WEBHOOK_URL

# Status code mapping for log messages
STATUS_MAP = {
    -1: {"color": 0x95A5A6, "title": "No Code"},   # Gray
    0: {"color": 0x3498DB, "title": "Info"},       # Blue
    1: {"color": 0xF1C40F, "title": "Warning"},    # Yellow
    2: {"color": 0xE74C3C, "title": "Error"},      # Red
    3: {"color": 0x2ECC71, "title": "Success"},    # Green
    4: {"color": 0x9B59B6, "title": "Debug"}       # Purple
}

def webhook_log(embed_message: str, status: int = -1, non_embed_message: str = None):
    """
    Sends a log message to a Discord webhook with formatting.

    Args:
        message: Message to send.
        status: Status of Message (-1: Debug, 0: Info, 1: Warning, 2: Error).

    Returns:
        None
    """
    # Skip if no webhook URL configured
    if not WEBHOOK_URL:
        return
        
    # Get status color and title
    status_info = STATUS_MAP.get(status, STATUS_MAP[-1])
    
    # Create embed
    embed = {
        "title": status_info["title"],
        "description": embed_message,
        "color": status_info["color"],
        "timestamp": datetime.datetime.now().isoformat()
    }
    
    # Create payload
    payload = {
        "embeds": [embed]
    }
    
    # Add non-embed message if provided
    if non_embed_message:
        payload["content"] = non_embed_message
    
    # Send webhook asynchronously
    def send_webhook():
        try:
            pass
            requests.post(WEBHOOK_URL, json=payload)
        except Exception as e:
            print(f"Error sending webhook: {str(e)}")
    
    # Start thread to send webhook
    threading.Thread(target=send_webhook).start()
