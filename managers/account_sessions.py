"""Bind sessions to one account and its current password credential."""

import hashlib
import hmac
from flask import current_app, session
from .database_manager import DatabaseManager


def credential_stamp(user_id, password_hash):
    return hashlib.sha256(f"{user_id}:{password_hash}".encode()).hexdigest()


def establish_session(email, account):
    session.clear()
    session.update(email=email, account_id=int(account[0]),
                   credential_stamp=credential_stamp(account[0], account[9]),
                   pterodactyl_id=(account[5],))
    regenerate = getattr(current_app.session_interface, "regenerate", None)
    if regenerate:
        regenerate(session)


def session_is_valid():
    email = session.get("email")
    if not email:
        return False
    account = DatabaseManager.execute_query(
        "SELECT id, password, pterodactyl_id, suspended FROM users WHERE email = %s", (email,)
    )
    if (not account or account[3] or session.get("account_id") != account[0]
            or not hmac.compare_digest(str(session.get("credential_stamp", "")),
                                       credential_stamp(account[0], account[1]))):
        session.clear()
        return False
    session["pterodactyl_id"] = (account[2],)
    return True
