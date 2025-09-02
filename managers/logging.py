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
from config import WEBHOOK_URL, TICKET_WEBHOOK_URL
from .database_manager import DatabaseManager

# Status code mapping for log messages
STATUS_MAP = {
    -1: {"color": 0x95A5A6, "title": "No Code"},   # Gray
    0: {"color": 0x3498DB, "title": "Info"},       # Blue
    1: {"color": 0xF1C40F, "title": "Warning"},    # Yellow
    2: {"color": 0xE74C3C, "title": "Error"},      # Red
    3: {"color": 0x2ECC71, "title": "Success"},    # Green
    4: {"color": 0x9B59B6, "title": "Debug"}       # Purple
}

def webhook_log(embed_message: str, status: int = -1, non_embed_message: str = None, is_ticket: bool = False, database_log: bool = False):
    """
    Sends a log message to a Discord webhook with formatting and logs to activity_logs table.

    Args:
        embed_message: Message to send in the embed.
        status: Status of Message (-1: Debug, 0: Info, 1: Warning, 2: Error).
        non_embed_message: Optional message to send outside the embed.
        is_ticket: Whether this log is related to tickets (uses TICKET_WEBHOOK_URL if True).

    Returns:
        None
    """
    # Determine which webhook URL to use
    webhook_url = TICKET_WEBHOOK_URL if is_ticket and TICKET_WEBHOOK_URL else WEBHOOK_URL
    
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
    
    # Log to activity_logs table
    log_content = json.dumps({
        "status": status_info["title"],
        "message": embed_message,
        "non_embed_message": non_embed_message,
        "is_ticket": is_ticket,
        "timestamp": datetime.datetime.now().isoformat()
    })
    
    if database_log:
        try:
            # Insert into activity_logs table
            query = "INSERT INTO activity_logs (create_time, content) VALUES (%s, %s)"
            values = (datetime.datetime.now(), log_content)
            DatabaseManager.execute_query(query, values)
        except Exception as e:
            print(f"Error logging to activity_logs: {str(e)}")
    
    # Skip webhook if no URL configured
    if not webhook_url:
        return
    
    # Send webhook asynchronously
    def send_webhook():
        try:
            requests.post(webhook_url, json=payload, timeout=60)
        except Exception as e:
            print(f"Error sending webhook: {str(e)}")
    
    # Start thread to send webhook
    threading.Thread(target=send_webhook).start()
