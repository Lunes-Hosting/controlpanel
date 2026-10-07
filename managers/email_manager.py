"""
Email Management Module
=================

This module handles all email-related operations including:
- Sending general emails
- Verification emails
- Password reset emails
- Token generation for verification and reset

Functions in this module interact with Flask-Mail to send emails to users.
"""

import threading
import secrets
import string
from html import escape
from flask_mail import Mail, Message
from flask import url_for, current_app
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def _render_email_html(title: str, message: str, action_label: str = None, action_url: str = None):
    """Render a compact, email-client-friendly Lunes Hosting notification."""
    safe_title = escape(title)
    safe_message = escape(message).replace("\n", "<br>")
    action = ""
    if action_label and action_url:
        action = f"""
            <tr>
                <td align="center" style="padding: 28px 0 8px;">
                    <a href="{escape(action_url, quote=True)}"
                       style="display: inline-block; padding: 14px 24px; border-radius: 8px;
                              background-color: #2563eb; color: #ffffff; font-size: 15px;
                              font-weight: 700; text-decoration: none;">
                        {escape(action_label)}
                    </a>
                </td>
            </tr>
            <tr>
                <td style="padding: 12px 0 0; color: #64748b; font-size: 12px; line-height: 1.6;
                           overflow-wrap: anywhere;">
                    If the button does not work, copy and paste this link into your browser:<br>
                    <a href="{escape(action_url, quote=True)}"
                       style="color: #2563eb; text-decoration: underline;">
                        {escape(action_url)}
                    </a>
                </td>
            </tr>
        """

    return f"""<!doctype html>
<html lang="en">
<head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{safe_title} | Lunes Hosting</title>
</head>
<body style="margin: 0; padding: 0; background-color: #0b1220;
             color: #e2e8f0; font-family: Arial, Helvetica, sans-serif;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
           style="background-color: #0b1220; padding: 36px 16px;">
        <tr>
            <td align="center">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0"
                       style="max-width: 600px; background-color: #111c2f;
                              border: 1px solid #23324a; border-radius: 14px; overflow: hidden;">
                    <tr>
                        <td style="padding: 24px 32px; background-color: #0f172a;
                                   border-bottom: 1px solid #263650;">
                            <p style="margin: 0; color: #93c5fd; font-size: 13px;
                                      font-weight: 700; letter-spacing: 2px; text-transform: uppercase;">
                                Lunes Hosting
                            </p>
                        </td>
                    </tr>
                    <tr>
                        <td style="padding: 36px 32px 40px;">
                            <p style="margin: 0 0 10px; color: #60a5fa; font-size: 12px;
                                      font-weight: 700; letter-spacing: 1.5px; text-transform: uppercase;">
                                Account notification
                            </p>
                            <h1 style="margin: 0 0 20px; color: #f8fafc; font-size: 26px;
                                       line-height: 1.25;">{safe_title}</h1>
                            <p style="margin: 0; color: #cbd5e1; font-size: 15px;
                                      line-height: 1.8;">{safe_message}</p>
                            <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0">
                                {action}
                            </table>
                        </td>
                    </tr>
                </table>
                <p style="max-width: 560px; margin: 20px auto 0; color: #94a3b8;
                          font-size: 12px; line-height: 1.7; text-align: center;">
                    You are receiving this email because you have an account with Lunes Hosting.
                    If you need help, please contact our support team.
                </p>
            </td>
        </tr>
    </table>
</body>
</html>"""


def _plain_email_body(message: str, action_label: str = None, action_url: str = None):
    body = message
    if action_label and action_url:
        body = f"{body}\n\n{action_label}: {action_url}"
    return f"Lunes Hosting\n\n{body}\n\nYou are receiving this email because you have an account with Lunes Hosting."


def send_email(email: str, title: str, message: str, inner_app,
               action_label: str = None, action_url: str = None):
    """
    Sends an email to the user asynchronously using APScheduler.
    
    Args:
        email: User's email
        title: Email title
        message: Email message
        inner_app: Flask app
    
    Returns:
        None
    """
    def send_async_email(app, msg):
        with app.app_context():
            mail = Mail(app)
            mail.send(msg)
    
    with inner_app.app_context():
        msg = Message(title, sender=inner_app.config['MAIL_DEFAULT_SENDER'], recipients=[email])
        msg.body = _plain_email_body(message, action_label, action_url)
        msg.html = _render_email_html(title, message, action_label, action_url)
        
        # Start a thread to send the email
        threading.Thread(target=send_async_email, args=(inner_app, msg)).start()

def generate_verification_token():
    """
    Generates a verification token.
    
    Returns:
        str: Verification token
    """
    # Generate a secure random token
    token = ''.join(secrets.SystemRandom().choices(string.ascii_letters + string.digits, k=64))
    return token

def send_verification_email(email, verification_token, inner_app):
    """
    Sends a verification email to the user.
    
    Args:
        email: User's email
        verification_token: Verification token
        inner_app: Flask app
    
    Returns:
        None
    """
    try:
        # Determine the base URL based on environment
        if inner_app.config.get('DEBUG_FRONTEND_MODE', False):
            # Development environment
            base_url = "http://127.0.0.1:3040"
        else:
            # Production environment
            base_url = "https://betadash.lunes.host"
            
        verification_url = f"{base_url}/verify_email/{verification_token}"
        
        message = "Please verify your email address to finish setting up your Lunes Hosting account."
        send_email(
            email, "Email Verification", message, inner_app,
            action_label="Verify Email", action_url=verification_url,
        )
    except Exception as e:
        print(f"Error sending verification email: {str(e)}")

def generate_reset_token():
    """
    Generates a reset token.
    
    Returns:
        str: Reset token
    """
    # Generate a secure random token
    token = ''.join(secrets.SystemRandom().choices(string.ascii_letters + string.digits, k=64))
    return token

def send_reset_email(email: str, reset_token: str, inner_app):
    """
    Sends a password reset email to the user.
    
    Args:
        email: User's email
        reset_token: Reset token
        inner_app: Flask app
    
    Returns:
        None
    """
    try:
        # Determine the base URL based on environment
        if inner_app.config.get('DEBUG_FRONTEND_MODE', False):
            # Development environment
            base_url = "http://127.0.0.1:3040"
        else:
            # Production environment
            base_url = "https://betadash.lunes.host"
            
        reset_url = f"{base_url}/reset_password/{reset_token}"
        
        message = (
            "We received a request to reset the password for your Lunes Hosting account. "
            "Use the button below to choose a new password. If you did not request this, "
            "you can safely ignore this email."
        )
        send_email(
            email, "Password Reset", message, inner_app,
            action_label="Reset Password", action_url=reset_url,
        )
    except Exception as e:
        print(f"Error sending reset email: {str(e)}")


def send_email_without_app_context(email: str, title: str, message: str, smtp_config):
    """
    Sends an email without requiring a Flask application context.
    Useful for sending emails from external processes like Discord bots.
    
    Args:
        email: User's email
        title: Email title
        message: Email message
        smtp_config: Dictionary containing SMTP configuration with keys:
                    - MAIL_SERVER
                    - MAIL_PORT
                    - MAIL_USERNAME
                    - MAIL_PASSWORD
                    - MAIL_DEFAULT_SENDER
                    - MAIL_USE_TLS (optional, defaults to True)
    
    Returns:
        None
    """
    def send_async_email(smtp_config, recipient, subject, body):
        try:
            # Create a multipart message
            msg = MIMEMultipart('alternative')
            msg['From'] = smtp_config['MAIL_DEFAULT_SENDER']
            msg['To'] = recipient
            msg['Subject'] = subject
            
            # Include plain-text and branded HTML versions for clients with different capabilities.
            text_part = MIMEText(_plain_email_body(body), 'plain')
            msg.attach(text_part)
            
            html_part = MIMEText(_render_email_html(title, body), 'html')
            msg.attach(html_part)
            
            # Connect to SMTP server
            server = smtplib.SMTP(smtp_config['MAIL_SERVER'], smtp_config['MAIL_PORT'])
            
            # Use TLS if specified (default is True)
            use_tls = smtp_config.get('MAIL_USE_TLS', True)
            if use_tls:
                server.starttls()
            
            # Login if credentials are provided
            if smtp_config.get('MAIL_USERNAME') and smtp_config.get('MAIL_PASSWORD'):
                server.login(smtp_config['MAIL_USERNAME'], smtp_config['MAIL_PASSWORD'])
            
            # Send email
            server.send_message(msg)
            server.quit()
            print(f"Email sent to {recipient} successfully")
        except Exception as e:
            print(f"Error sending email: {str(e)}")
    
    # Start a thread to send the email asynchronously
    threading.Thread(target=send_async_email, args=(smtp_config, email, title, message)).start()
