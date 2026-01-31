"""
Admin Stats Routes
=================

Provides aggregated platform statistics for administrators.

- Total users
- Total clients
- Total servers (from Pterodactyl)
- Total free vs paid servers
- Plan popularity (bar chart data)

Templates:
- admin/stats.html

Access Control:
- Protected by admin_required
"""

from flask import render_template
from Routes.admin import admin
from managers.authentication import admin_required
from managers.database_manager import DatabaseManager
from managers.credit_manager import convert_to_product
from managers.utils import HEADERS
from config import PTERODACTYL_URL
from security import safe_requests

def shorten_number(n):
    """
    Format large numbers into human readable strings (k, M, B, T).
    """
    try:
        n = float(n)
    except (ValueError, TypeError):
        return "0"
        
    if n >= 1_000_000_000_000:
        return f'{n/1_000_000_000_000:.2f}T'
    if n >= 1_000_000_000:
        return f'{n/1_000_000_000:.2f}B'
    if n >= 1_000_000:
        return f'{n/1_000_000:.2f}M'
    if n >= 1_000:
        return f'{n/1_000:.2f}K'
    return f'{n:.2f}'

@admin.route("/stats")
@admin_required
def admin_stats():
    """
    Render the admin statistics dashboard with totals and chart data.
    """
    # Totals from DB
    total_users = DatabaseManager.execute_query("SELECT COUNT(*) FROM users")
    total_users = int(total_users[0]) if total_users else 0

    total_clients = DatabaseManager.execute_query(
        "SELECT COUNT(*) FROM users WHERE role = 'client'"
    )
    total_clients = int(total_clients[0]) if total_clients else 0

    # Tickets and messages
    total_tickets = DatabaseManager.execute_query("SELECT COUNT(*) FROM tickets")
    total_tickets = int(total_tickets[0]) if total_tickets else 0

    total_ticket_messages = DatabaseManager.execute_query("SELECT COUNT(*) FROM ticket_comments")
    total_ticket_messages = int(total_ticket_messages[0]) if total_ticket_messages else 0

    # Total credits in circulation (exclude admins)
    credits_circ = DatabaseManager.execute_query("SELECT COALESCE(SUM(credits), 0) FROM users WHERE role != 'admin'")
    total_credits_circulation = float(credits_circ[0]) if credits_circ else 0.0

    # Servers from Pterodactyl
    servers_resp = safe_requests.get(
        f"{PTERODACTYL_URL}api/application/servers?per_page=100000",
        headers=HEADERS,
        timeout=60,
    )

    total_servers = 0
    free_servers = 0
    paid_servers = 0
    plan_counts: dict[str, int] = {}
    total_monthly_credits_used = 0.0

    if servers_resp.status_code == 200:
        servers_json = servers_resp.json()
        data = servers_json.get("data", [])
        total_servers = len(data)
        for s in data:
            try:
                product = convert_to_product(s)
                price = float(product.get("price", 0) or 0)
                name = product.get("name", "Unknown")
                if price == 0:
                    free_servers += 1
                else:
                    paid_servers += 1
                total_monthly_credits_used += price
                plan_counts[name] = plan_counts.get(name, 0) + 1
            except Exception:
                # If mapping fails, count under Unknown
                paid_servers += 0  # no-op to keep structure
                plan_counts["Unknown"] = plan_counts.get("Unknown", 0) + 1
    else:
        # If API fails, keep zeros and show empty chart
        data = []

    # Prepare chart data
    chart_labels = list(plan_counts.keys())
    chart_values = [plan_counts[k] for k in chart_labels]

    return render_template(
        "admin/stats.html",
        total_users=total_users,
        total_clients=total_clients,
        total_servers=total_servers,
        free_servers=free_servers,
        paid_servers=paid_servers,
        total_tickets=total_tickets,
        total_ticket_messages=total_ticket_messages,
        total_credits_circulation=shorten_number(total_credits_circulation),
        total_monthly_credits_used=shorten_number(total_monthly_credits_used),
        chart_labels=chart_labels,
        chart_values=chart_values,
    )
