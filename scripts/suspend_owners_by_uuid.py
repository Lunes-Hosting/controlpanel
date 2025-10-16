#!/usr/bin/env python3
import sys
import argparse
from pathlib import Path
from typing import Dict, List, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from managers.database_manager import DatabaseManager
from managers.utils import HEADERS
from config import PTERODACTYL_URL
from security import safe_requests


def read_uuid_file(path: Path) -> List[str]:
    with path.open("r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def fetch_target_servers(uuids: Set[str]) -> Tuple[int, Dict[str, dict]]:
    resp = safe_requests.get(
        f"{PTERODACTYL_URL}api/application/servers?per_page=100000",
        headers=HEADERS,
        timeout=60,
    )
    if resp.status_code != 200:
        return resp.status_code, {}
    payload = resp.json() or {}
    result: Dict[str, dict] = {}
    for entry in payload.get("data", []):
        attributes = entry.get("attributes", {})
        server_uuid = attributes.get("uuid")
        if server_uuid in uuids and server_uuid not in result:
            result[server_uuid] = attributes
    return 200, result


def suspend_panel_user(panel_id: int, apply: bool) -> Tuple[str, str]:
    row = DatabaseManager.execute_query(
        "SELECT id, email, suspended, role FROM users WHERE pterodactyl_id = %s",
        (panel_id,),
    )
    if not row:
        return "missing_user", ""
    user_id, email, suspended_flag, role = row
    if role in {"client", "admin", "support"}:
        return "skipped_role", email or f"panel_id:{panel_id}"
    if suspended_flag:
        return "already_suspended", email or f"panel_id:{panel_id}"
    if not apply:
        return "dry_run", email or f"panel_id:{panel_id}"
    DatabaseManager.execute_query(
        "UPDATE users SET suspended = 1 WHERE id = %s",
        (user_id,),
    )
    return "suspended", email or f"panel_id:{panel_id}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Suspend owners of servers listed by UUID")
    parser.add_argument("file", nargs="?", default=PROJECT_ROOT / "servers.txt")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"Input file not found: {file_path}")
        return 1

    uuids = read_uuid_file(file_path)
    if not uuids:
        print("No UUIDs to process.")
        return 0

    status, servers = fetch_target_servers(set(uuids))
    if status != 200:
        print(f"Failed to fetch servers from Pterodactyl (status {status}).")
        return 1

    processed_users: Set[int] = set()
    suspended_emails: List[str] = []
    dry_run_emails: List[str] = []
    already_suspended: List[str] = []
    skipped_roles: List[str] = []
    missing_servers: List[str] = []
    unmatched_users: List[Tuple[str, int]] = []

    for uuid in uuids:
        attributes = servers.get(uuid)
        if not attributes:
            missing_servers.append(uuid)
            continue
        owner_id = attributes.get("user")
        if owner_id is None:
            missing_servers.append(uuid)
            continue
        if owner_id in processed_users:
            continue
        status, email = suspend_panel_user(int(owner_id), args.apply)
        if status == "missing_user":
            unmatched_users.append((uuid, int(owner_id)))
        elif status == "already_suspended":
            if email and email not in already_suspended:
                already_suspended.append(email)
        elif status == "dry_run":
            if email and email not in dry_run_emails:
                dry_run_emails.append(email)
        elif status == "suspended":
            if email and email not in suspended_emails:
                suspended_emails.append(email)
        elif status == "skipped_role":
            if email and email not in skipped_roles:
                skipped_roles.append(email)
        processed_users.add(int(owner_id))

    if not args.apply:
        if dry_run_emails:
            print("Dry run: owners that would be suspended:")
            for email in dry_run_emails:
                print(f"  {email}")
            print(f"Total owners to suspend: {len(dry_run_emails)}")
        else:
            print("Dry run: no owners would be suspended.")
    else:
        if suspended_emails:
            print("Suspended users:")
            for email in suspended_emails:
                print(f"  {email}")
            print(f"Total owners suspended: {len(suspended_emails)}")
        else:
            print("No owners were suspended.")

    if already_suspended:
        print("Already suspended:")
        for email in already_suspended:
            print(f"  {email}")
    if skipped_roles:
        print("Skipped due to role (client/admin/support):")
        for email in skipped_roles:
            print(f"  {email}")
    if missing_servers:
        print("Server UUIDs not found:")
        for uuid in missing_servers:
            print(f"  {uuid}")
    if unmatched_users:
        print("Owners not found in local database:")
        for uuid, owner_id in unmatched_users:
            print(f"  uuid={uuid} owner_id={owner_id}")

    print(f"Processed {len(processed_users)} unique owners from {len(uuids)} UUIDs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
