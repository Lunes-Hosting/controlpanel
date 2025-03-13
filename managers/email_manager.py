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
import random
from flask_mail import Mail, Message
from flask import url_for

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
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=64))
    return token

def send_verification_email(email: str, verification_token: str, inner_app):
    """
    Sends a verification email to the user.
    
    Args:
        email: User's email
        verification_token: Verification token
        inner_app: Flask app
    
    Returns:
        None
    """
    verification_url = url_for('verify_email', token=verification_token, _external=True)
    message = f"Please verify your email by clicking on the following link: <a href='{verification_url}'>Verify Email</a>"
    send_email(email, "Email Verification", message, inner_app)

def generate_reset_token():
    """
    Generates a reset token.
    
    Returns:
        str: Reset token
    """
    # Generate a secure random token
    token = ''.join(random.choices(string.ascii_letters + string.digits, k=64))
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
    reset_url = url_for('reset_password', token=reset_token, _external=True)
    message = f"Please reset your password by clicking on the following link: <a href='{reset_url}'>Reset Password</a>"
    send_email(email, "Password Reset", message, inner_app)
