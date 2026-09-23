"""Safe, display-only connection details for rejected signup attempts.

Cloudflare location headers are estimates. These values must never be used
for account decisions, and the origin should accept them only through a
trusted Cloudflare/proxy deployment.
"""

import ipaddress
import re


def _place_part(value):
    if not isinstance(value, str):
        return None
    value = " ".join(value.split())
    if not value or len(value) > 80:
        return None
    if not all(char.isalnum() or char in " .,'’()-" for char in value):
        return None
    return value


def connection_details(headers, client_ip):
    """Return a public IP and optional approximate Cloudflare location."""
    try:
        address = ipaddress.ip_address(client_ip)
    except (ValueError, TypeError):
        return {"ip": None, "location": None}
    if not address.is_global:
        return {"ip": None, "location": None}

    country = (headers.get("CF-IPCountry") or "").upper().strip()
    if not re.fullmatch(r"[A-Z]{2}", country) or country == "XX":
        country = None
    city = _place_part(headers.get("CF-IPCity"))
    region = _place_part(headers.get("CF-Region"))
    parts = []
    for part in (city, region, country):
        if part and part not in parts:
            parts.append(part)
    return {"ip": str(address), "location": ", ".join(parts) or None}
