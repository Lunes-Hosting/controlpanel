#!/usr/bin/env python3
"""
Email Notification Script
========================

This script sends email notifications to all verified users without requiring the Flask app to be running.
It implements rate limiting to send a maximum of 4000 emails per hour.

Usage:
    python email_notifications.py --subject "Your Subject" --message "Your message content"

Options:
    --subject    Email subject line
    --message    Email message content (can include HTML)
    --test       Send to only 5 users (for testing)
    --help       Show this help message
"""

import argparse
import time
import threading
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import mysql.connector
from datetime import datetime
import sys
import os

# Import config - assuming config.py exists with the same variables as configexample.py
try:
    from config import (
        HOST, USER, PASSWORD, DATABASE,
        MAIL_SERVER, MAIL_PORT, MAIL_USERNAME, MAIL_PASSWORD, MAIL_DEFAULT_SENDER
    )
except ImportError:
    print("Error: Could not import configuration. Make sure config.py exists.")
    sys.exit(1)

# Email rate limiting constants
MAX_EMAILS_PER_HOUR = 500
SECONDS_PER_EMAIL = 3600 / MAX_EMAILS_PER_HOUR  # Time between emails to maintain rate limit

def get_db_connection():
    """Create and return a database connection"""
    try:
        connection = mysql.connector.connect(
            host=HOST,
            user=USER,
            password=PASSWORD,
            database=DATABASE,
            charset='utf8mb4',
            collation='utf8mb4_unicode_ci'
        )
        cursor = connection.cursor(buffered=True)
        return connection, cursor
    except mysql.connector.Error as err:
        print(f"Database connection error: {err}")
        sys.exit(1)

def get_verified_users(limit=None):
    """
    Get all users with verified emails
    
    Args:
        limit: Optional limit for testing purposes
        
    Returns:
        List of tuples containing (email, name)
    """
    connection, cursor = get_db_connection()
    
    try:
        query = "SELECT email, name FROM users WHERE email_verified_at IS NOT NULL"
        if limit:
            query += f" LIMIT {limit}"
            
        cursor.execute(query)
        users = cursor.fetchall()
        return users
    except mysql.connector.Error as err:
        print(f"Error retrieving users: {err}")
        return []
    finally:
        cursor.close()
        connection.close()

def send_email(recipient, subject, body):
    """
    Send an email to a single recipient
    
    Args:
        recipient: Email address of recipient
        subject: Email subject
        body: Email body (can include HTML)
        
    Returns:
        bool: True if successful, False otherwise
    """
    try:
        # Create a multipart message
        msg = MIMEMultipart('alternative')
        msg['From'] = MAIL_DEFAULT_SENDER
        msg['To'] = recipient
        msg['Subject'] = subject
        
        # Create plain text version by removing HTML tags if present
        plain_text = body.replace('<a href=', '').replace('</a>', '').replace('>', ': ')
        
        # Add plain text part first (will be displayed if HTML is not supported)
        text_part = MIMEText(plain_text, 'plain')
        msg.attach(text_part)
        
        # Add HTML part second (will be preferred by most mail clients)
        html_part = MIMEText(f"<html><body>{body}</body></html>", 'html')
        msg.attach(html_part)
        
        # Connect to SMTP server
        server = smtplib.SMTP(MAIL_SERVER, MAIL_PORT)
        
        # Use TLS if available
        try:
            server.starttls()
        except smtplib.SMTPNotSupportedError:
            pass
        
        # Login if credentials are provided
        if MAIL_USERNAME and MAIL_PASSWORD:
            server.login(MAIL_USERNAME, MAIL_PASSWORD)
        
        # Send email
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        print(f"Error sending email to {recipient}: {str(e)}")
        return False

def send_bulk_emails(users, subject, message):
    """
    Send emails to all users with rate limiting
    
    Args:
        users: List of (email, name) tuples
        subject: Email subject
        message: Email message template
        
    Returns:
        None
    """
    total_users = len(users)
    successful = 0
    failed = 0
    
    print(f"Starting to send emails to {total_users} users at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Rate limiting to {MAX_EMAILS_PER_HOUR} emails per hour ({SECONDS_PER_EMAIL:.2f} seconds between emails)")
    
    start_time = time.time()
    
    for i, (email, name) in enumerate(users, 1):
        # Personalize message with user's name
        personalized_message = message.replace("{name}", name) if name else message
        
        # Send email
        success = send_email(email, subject, personalized_message)
        
        if success:
            successful += 1
            print(f"[{i}/{total_users}] Email sent to {email}")
        else:
            failed += 1
            print(f"[{i}/{total_users}] Failed to send email to {email}")
        
        # Calculate progress and estimated time remaining
        if i % 10 == 0 or i == total_users:
            elapsed = time.time() - start_time
            emails_per_second = i / elapsed if elapsed > 0 else 0
            remaining = (total_users - i) / emails_per_second if emails_per_second > 0 else 0
            
            hours, remainder = divmod(remaining, 3600)
            minutes, seconds = divmod(remainder, 60)
            
            print(f"Progress: {i}/{total_users} ({i/total_users*100:.1f}%)")
            print(f"Estimated time remaining: {int(hours)}h {int(minutes)}m {int(seconds)}s")
        
        # Rate limiting - wait between emails if not the last one
        if i < total_users:
            time.sleep(SECONDS_PER_EMAIL)
    
    # Print summary
    total_time = time.time() - start_time
    print("\nEmail sending completed")
    print(f"Total time: {total_time/60:.1f} minutes")
    print(f"Successful: {successful}")
    print(f"Failed: {failed}")

def main():
    """Main function to parse arguments and send emails"""
    parser = argparse.ArgumentParser(description="Send email notifications to all verified users")
    parser.add_argument("--subject", required=True, help="Email subject line")
    parser.add_argument("--message", required=True, help="Email message content (can include HTML)")
    parser.add_argument("--test", action="store_true", help="Send to only 5 users (for testing)")
    
    args = parser.parse_args()
    
    # Get verified users
    limit = 5 if args.test else None
    users = get_verified_users(limit)
    
    if not users:
        print("No verified users found")
        return
    
    print(f"Found {len(users)} verified users")
    
    # Confirm before sending
    if not args.test:
        confirm = input(f"Are you sure you want to send emails to {len(users)} users? (y/n): ")
        if confirm.lower() != 'y':
            print("Operation cancelled")
            return
    
    # Send emails
    send_bulk_emails(users[1172:], args.subject, args.message)

if __name__ == "__main__":

    main()
