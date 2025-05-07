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
from flask_mail import Mail, Message
from flask import url_for, current_app
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

def send_email(email: str, title: str, message: str, inner_app):
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
        msg.body = message
        msg.html = f"<p>{message}</p>"
        
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
        
        message = f"Please verify your email by clicking on the following link: <a href='{verification_url}'>Verify Email</a>"
        send_email(email, "Email Verification", message, inner_app)
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
        
        message = f"Please reset your password by clicking on the following link: <a href='{reset_url}'>Reset Password</a>"
        send_email(email, "Password Reset", message, inner_app)
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
            
            # Create plain text version by removing HTML tags if present
            plain_text = body.replace('<a href=', '').replace('</a>', '').replace('>', ': ')
            
            # Add plain text part first (will be displayed if HTML is not supported)
            text_part = MIMEText(plain_text, 'plain')
            msg.attach(text_part)
            
            # Add HTML part second (will be preferred by most mail clients)
            html_part = MIMEText(f"<html><body>{body}</body></html>", 'html')
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
