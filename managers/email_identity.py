"""Use the email index only for candidates; compare actual identities in Python."""

from runtime.email_identity import canonical_email, email_lookup_candidates
from .database_manager import DatabaseManager


def find_user_by_email(value):
    try:
        canonical, unicode_form = email_lookup_candidates(value)
    except ValueError:
        return None
    # The existing Unicode collation can return extra candidates. It must never
    # determine which mailbox owns the account.
    rows = DatabaseManager.execute_query(
        'SELECT * FROM users WHERE email IN (%s, %s)',
        (canonical, unicode_form), fetch_all=True)
    matches = []
    for row in rows or ():
        try:
            if canonical_email(row[7]) == canonical:
                matches.append(row)
        except ValueError:
            continue
    return matches[0] if len(matches) == 1 else None
