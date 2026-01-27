#!/usr/bin/env python3
"""
Email Notification Script
========================

This script sends email notifications to all verified users without requiring the Flask app to be running.
It implements rate limiting to send a maximum of 4000 emails per hour.

Usage:
    python email_notifications.py --subject "Your Subject" --message "Your message content"

usage here python email_notifications.py --subject "Celebrating 30,000 Users + Massive Giveaway!" --message "<b>We hit a huge milestone - 30,000 users on Lunes Host LLC!</b> <br><br>To celebrate this achievement and thank you for being part of our journey, we are hosting a massive giveaway. <br><br>We are giving away <b>100 credits to 30 winners</b>! <br><br>Join the giveaway channel on our Discord to enter: <a href='https://discord.gg/MHEAwNjKb2'>https://discord.gg/MHEAwNjKb2</a>" --start-index 1400
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
        query = "SELECT email, name FROM users WHERE email_verified_at IS NOT NULL ORDER BY email ASC"
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


def get_email_template(content, title="Notification"):
    """
    Wrap content in a professional HTML email template
    """
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <style>
            body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 0; background-color: #f4f4f7; color: #51545E; }}
            .email-wrapper {{ width: 100%; background-color: #f4f4f7; padding: 40px 20px; }}
            .email-content {{ max-width: 600px; margin: 0 auto; background-color: #ffffff; border-radius: 8px; box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05); overflow: hidden; }}
            .email-header {{ background-color: #2d3748; padding: 30px; text-align: center; color: #ffffff; }}
            .email-header h1 {{ margin: 0; font-size: 24px; font-weight: 600; letter-spacing: 1px; }}
            .email-body {{ padding: 40px; color: #333333; line-height: 1.6; font-size: 16px; }}
            .email-footer {{ text-align: center; padding: 24px; font-size: 12px; color: #a8aaaf; }}
            .email-footer p {{ margin: 5px 0; }}
            a {{ color: #3869d4; text-decoration: none; }}
            a:hover {{ text-decoration: underline; }}
        </style>
    </head>
    <body>
        <div class="email-wrapper">
            <div class="email-content">
                <div class="email-header">
                    <h1>Lunes Hosting</h1>
                </div>
                <div class="email-body">
                    {content}
                </div>
            </div>
            <div class="email-footer">
                <p>&copy; 2025 Lunes Hosting. All rights reserved.</p>
                <p>You are receiving this email because you have an account with Lunes Hosting.</p>
            </div>
        </div>
    </body>
    </html>
    """

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
        
        # Create plain text version
        # Improve simple cleaning: handle breaks and links
        plain_text = body.replace('<br>', '\n').replace('<br/>', '\n').replace('<br />', '\n')
        plain_text = plain_text.replace('<b>', '**').replace('</b>', '**')
        # Simple link extraction: <a href='url'>text</a> -> text (url)
        # This is a very basic regex-free approach, might need robustness for complex attributes
        while '<a href=' in plain_text:
            start = plain_text.find('<a href=')
            end = plain_text.find('</a>', start)
            if start != -1 and end != -1:
                link_tag = plain_text[start:end+4]
                # Extract url (handling quotes)
                url_start = link_tag.find('href=') + 5
                quote = link_tag[url_start]
                if quote in ["'", '"']:
                    url_end = link_tag.find(quote, url_start + 1)
                    url = link_tag[url_start+1:url_end]
                else:
                    # No quotes? find space or >
                    url_end = link_tag.find('>', url_start)
                    url = link_tag[url_start:url_end]
                
                # Extract text
                text_start = link_tag.find('>') + 1
                text_end = link_tag.find('</a>') - start # relative to link_tag start? No.
                # simpler:
                full_tag_content = plain_text[start:end+4]
                inner_text_start = full_tag_content.find('>') + 1
                inner_text = full_tag_content[inner_text_start:-4]
                
                replacement = f"{inner_text} ({url})"
                plain_text = plain_text.replace(full_tag_content, replacement, 1)
            else:
                break

        # Strip remaining tags
        while '<' in plain_text and '>' in plain_text:
            start = plain_text.find('<')
            end = plain_text.find('>', start)
            if start != -1 and end != -1:
                plain_text = plain_text[:start] + plain_text[end+1:]
            else:
                break
        
        # Add plain text part first (will be displayed if HTML is not supported)
        text_part = MIMEText(plain_text, 'plain')
        msg.attach(text_part)
        
        # Add HTML part second (will be preferred by most mail clients)
        # Use the professional template
        full_html = get_email_template(body, subject)
        html_part = MIMEText(full_html, 'html')
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
    parser.add_argument("--start-index", type=int, default=0, help="Start sending from this index (0-based) to resume interrupted sends")
    
    args = parser.parse_args()
    
    # Get verified users
    limit = 5 if args.test else None
    users = get_verified_users(limit)
    
    if not users:
        print("No verified users found")
        return
    
    total_found = len(users)
    print(f"Found {total_found} verified users")
    
    # Handle start index
    if args.start_index > 0:
        if args.start_index >= total_found:
            print(f"Start index {args.start_index} is greater than or equal to total users {total_found}. Nothing to send.")
            return
        print(f"Resuming from index {args.start_index}. Skipping first {args.start_index} users.")
        users = users[args.start_index:]
        print(f"Will send to remaining {len(users)} users.")
    
    # Confirm before sending
    if not args.test:
        confirm = input(f"Are you sure you want to send emails to {len(users)} users? (y/n): ")
        if confirm.lower() != 'y':
            print("Operation cancelled")
            return
    
    # Send emails
    send_bulk_emails(users, args.subject, args.message)

if __name__ == "__main__":

    main()
