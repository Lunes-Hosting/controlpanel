"""Bind sessions to one account and its current password credential."""

import hashlib
import hmac
from flask import current_app, session, g
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
        "SELECT id, password, pterodactyl_id, suspended, role, credits, name, email_verified_at "
        "FROM users WHERE email = %s", (email,)
    )
    if (not account or account[3] or session.get("account_id") != account[0]
            or not hmac.compare_digest(str(session.get("credential_stamp", "")),
                                       credential_stamp(account[0], account[1]))):
        session.clear()
        return False
    if session.get('pterodactyl_id') not in ((account[2],), [account[2]]):
        session["pterodactyl_id"] = (account[2],)
    # Reuse only within this request. The next request still validates the live
    # credential, suspension flag and role; no privileges or balances get a TTL.
    g.setdefault('_account_roles', {})[email] = account[4]
    g.authenticated_account = (email, (account[5], account[2], account[6], account[7] is not None, account[3]))
    return True
