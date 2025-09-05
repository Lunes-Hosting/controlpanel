#!/usr/bin/env python3
"""
CLI: Paid Server & Client Ownership Stats
========================================

This CLI prints counts for:
- Total servers
- Free servers
- Paid servers
- Paid servers owned by users with role = 'client'
- Paid servers owned by non-clients (admins/support/regular users)

It reuses existing system logic:
- convert_to_product() from managers.credit_manager
- HEADERS from managers.utils
- PTERODACTYL_URL from config
- DatabaseManager for role lookups

Usage:
  python scripts/cli_paid_client_stats.py

Notes:
- Makes read-only requests to the Pterodactyl API.
- Performs read-only SELECTs to the local DB.
- No changes to functionality or data.
"""

import sys
from typing import Dict, Tuple
from pathlib import Path

# Ensure project root is on sys.path so imports like `managers.*` work when running directly
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from managers.credit_manager import convert_to_product
from managers.database_manager import DatabaseManager
from managers.utils import HEADERS
from security import safe_requests
from config import PTERODACTYL_URL


def fetch_all_servers() -> Tuple[int, list]:
    """Fetch all servers from Pterodactyl. Returns (status_code, data_list)."""
    resp = safe_requests.get(
        f"{PTERODACTYL_URL}api/application/servers?per_page=100000",
        headers=HEADERS,
        timeout=60,
    )
    if resp.status_code != 200:
        return resp.status_code, []
    payload = resp.json() or {}
    return 200, payload.get("data", [])


essential_user_fields = ("id", "email")

def fetch_user_email(user_id: int, cache: Dict[int, str]) -> str:
    """Map Pterodactyl user_id -> email using API, cached to avoid repeated calls."""
    if user_id in cache:
        return cache[user_id]
    resp = safe_requests.get(
        f"{PTERODACTYL_URL}api/application/users/{user_id}",
        headers=HEADERS,
        timeout=60,
    )
    if resp.status_code != 200:
        cache[user_id] = ""
        return ""
    data = resp.json() or {}
    attrs = data.get("attributes", {})
    email = attrs.get("email", "")
    cache[user_id] = email or ""
    return cache[user_id]


def get_role_by_email(email: str, cache: Dict[str, str]) -> str:
    """Lookup user's role from local DB (users table). Cached per email."""
    if email in cache:
        return cache[email]
    if not email:
        cache[email] = ""
        return ""
    row = DatabaseManager.execute_query(
        "SELECT role FROM users WHERE email = %s",
        (email,),
    )
    role = row[0] if row and len(row) > 0 else ""
    cache[email] = role
    return role


def main() -> int:
    status, servers = fetch_all_servers()
    if status != 200:
        print(f"Error: Failed to fetch servers from Pterodactyl (status {status})")
        return 1

    total_servers = len(servers)
    free_servers = 0
    paid_servers = 0
    paid_servers_owned_by_clients = 0
    paid_servers_owned_by_non_clients = 0
    paid_servers_suspended = 0

    user_email_cache: Dict[int, str] = {}
    role_cache: Dict[str, str] = {}

    for s in servers:
        try:
            product = convert_to_product(s)
            price = float(product.get("price", 0) or 0)
        except Exception:
            # If mapping fails, assume unknown plan with price 0
            price = 0.0

        if price <= 0:
            free_servers += 1
            continue

        # Paid
        paid_servers += 1

        # Determine owner -> email -> role
        try:
            owner_id = int(s["attributes"]["user"])
        except Exception:
            owner_id = None

        # Count suspended paid servers
        try:
            is_suspended = bool(s["attributes"].get("suspended", False))
        except Exception:
            is_suspended = False
        if is_suspended:
            paid_servers_suspended += 1

        owner_email = fetch_user_email(owner_id, user_email_cache) if owner_id is not None else ""
        role = get_role_by_email(owner_email, role_cache)

        if role == "client":
            paid_servers_owned_by_clients += 1
        else:
            paid_servers_owned_by_non_clients += 1

    # Output
    print("Paid Server & Client Ownership Stats")
    print("-----------------------------------")
    print(f"Total servers: {total_servers}")
    print(f"Free servers: {free_servers}")
    print(f"Paid servers: {paid_servers}")
    print(f"Paid servers (suspended): {paid_servers_suspended}")
    print("")
    print(f"Paid servers owned by clients: {paid_servers_owned_by_clients}")
    print(f"Paid servers owned by non-clients: {paid_servers_owned_by_non_clients}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
