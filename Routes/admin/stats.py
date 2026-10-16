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

from flask import render_template, jsonify
from Routes.admin import admin
from managers.authentication import admin_required
from managers.database_manager import DatabaseManager
from managers.credit_manager import convert_to_product
from managers.server_manager import iter_statistics_servers
from stats_snapshot import StatsSnapshot

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

def load_platform_stats():
    # Three user-table scans become one aggregate query.
    users = DatabaseManager.execute_query(
        "SELECT COUNT(*), COALESCE(SUM(role = 'client'), 0), "
        "COALESCE(SUM(CASE WHEN role != 'admin' THEN credits ELSE 0 END), 0) FROM users"
    )
    tickets = DatabaseManager.execute_query(
        "SELECT (SELECT COUNT(*) FROM tickets), (SELECT COUNT(*) FROM ticket_comments)"
    )
    result = dict(total_users=int(users[0]), total_clients=int(users[1]),
                  total_credits_circulation=shorten_number(users[2]),
                  total_tickets=int(tickets[0]), total_ticket_messages=int(tickets[1]),
                  total_servers=0, free_servers=0, paid_servers=0)
    counts = {}
    monthly = 0.0
    seen = set()
    for server in iter_statistics_servers():
        server_id = server['attributes']['id']
        if server_id in seen:
            continue
        seen.add(server_id)
        result['total_servers'] += 1
        try:
            product = convert_to_product(server)
            price = float(product.get('price', 0) or 0)
            name = product.get('name', 'Unknown')
        except (KeyError, TypeError, ValueError, AttributeError):
            name = 'Unknown'
        else:
            result['free_servers' if price == 0 else 'paid_servers'] += 1
            monthly += price
        counts[name] = counts.get(name, 0) + 1
    result.update(total_monthly_credits_used=shorten_number(monthly),
                  chart_labels=list(counts), chart_values=list(counts.values()))
    return result


platform_stats = StatsSnapshot(load_platform_stats)


@admin.route('/stats')
@admin_required
def admin_stats():
    state = platform_stats.get()
    return render_template('admin/stats.html', stats=state['data'] or {}, stats_state=state)


@admin.route('/stats/data')
@admin_required
def admin_stats_data():
    state = platform_stats.get()
    response = jsonify(state)
    response.headers['Cache-Control'] = 'no-store'
    return response, 200 if state['data'] is not None else (503 if state['error'] else 202)
