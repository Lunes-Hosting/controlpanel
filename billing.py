"""Balance estimates using the same plans and 30-day month as hourly billing."""

from decimal import Decimal, InvalidOperation


def match_product(server, products):
    memory = server['attributes']['limits']['memory']
    for product in products:
        if product['limits']['memory'] == memory:
            return product
    return min(products, key=lambda product: abs(product['limits']['memory'] - memory))


def format_credits(value):
    if 0 < value < Decimal('0.01'):
        return '<0.01'
    return f"{value:,.2f}".rstrip('0').rstrip('.')


def format_duration(hours):
    if hours < 1:
        return 'Less than 1 hour'
    if hours < 48:
        count = int(hours)
        return f"About {count:,} {'hour' if count == 1 else 'hours'}"
    days = f'{hours / 24:,.1f}'.rstrip('0').rstrip('.')
    return f'About {days} days'


def _amount(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount < 0:
        raise ValueError('Expected a non-negative credit amount')
    return amount


def balance_estimate(credits, servers, products):
    """None means server data could not be loaded; an empty list means no servers."""
    estimate = {
        'available': False,
        'state': 'unavailable',
        'duration': 'Estimate unavailable',
        'hours': None,
        'monthly_cost': None,
        'daily_cost': None,
        'coverage_percent': 0,
        'suspended_count': 0,
        'servers': [],
    }
    if servers is None:
        return estimate

    try:
        balance = _amount(credits)
        monthly_cost = Decimal(0)
        for server in servers:
            attributes = server['attributes']
            product = match_product(server, products)
            price = _amount(product['price'])
            suspended = attributes['suspended']
            if not isinstance(suspended, bool):
                raise ValueError('Missing server billing status')
            charge = Decimal(0) if suspended else price
            monthly_cost += charge
            estimate['suspended_count'] += int(suspended)
            estimate['servers'].append({
                'id': attributes['id'],
                'name': attributes['name'],
                'plan': product['name'],
                'suspended': suspended,
                'monthly_cost': float(charge),
                'monthly_label': format_credits(charge),
                'daily_label': format_credits(charge / 30),
            })

        estimate.update({
            'available': True,
            'balance': float(balance),
            'balance_label': format_credits(balance),
            'monthly_cost': float(monthly_cost),
            'monthly_label': format_credits(monthly_cost),
            'daily_cost': float(monthly_cost / 30),
            'daily_label': format_credits(monthly_cost / 30),
            'hourly_label': f'{monthly_cost / 720:,.4f}'.rstrip('0').rstrip('.'),
        })
        if monthly_cost == 0:
            estimate['state'] = 'paused' if estimate['suspended_count'] else 'free'
            estimate['duration'] = 'No current charges'
            return estimate

        # Multiply before dividing to avoid rounding repeating hourly prices.
        hours = balance * 720 / monthly_cost
        estimate['hours'] = float(hours)
        estimate['duration'] = format_duration(hours)
        estimate['coverage_percent'] = float(min(Decimal(100), hours / 720 * 100))
        if hours < 1:
            estimate['state'] = 'empty'
        elif hours <= 24:
            estimate['state'] = 'critical'
        elif hours <= 72:
            estimate['state'] = 'low'
        elif hours <= 168:
            estimate['state'] = 'week'
        else:
            estimate['state'] = 'healthy'
        return estimate
    except (KeyError, TypeError, ValueError, InvalidOperation, OverflowError):
        # Incomplete data must never look like free hosting or an affordable plan.
        estimate.update(available=False, state='unavailable', servers=[])
        return estimate


def topup_estimates(credits, servers, products, packages):
    estimates = {}
    for package in packages:
        try:
            balance = _amount(credits) + _amount(package['price'])
            estimate = balance_estimate(balance, servers, products)
            if estimate['available'] and estimate['monthly_cost']:
                estimate['extra_duration'] = format_duration(
                    _amount(package['price']) * 720 / Decimal(str(estimate['monthly_cost']))
                ).lower()
            estimates[package['price_link']] = estimate
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
    return estimates


def plan_estimates(credits, servers, products, server_id, plans):
    estimates = {}
    if servers is None:
        return estimates
    for plan in plans:
        try:
            # The existing update route charges one hour when a paid plan is selected.
            upfront = Decimal(0) if plan['limits']['memory'] == 128 else _amount(plan['price']) / 720
            remaining = _amount(credits) - upfront
            changed_servers = []
            found = False
            for server in servers:
                attributes = server['attributes']
                if str(attributes['id']) == str(server_id):
                    found = True
                    attributes = {**attributes, 'limits': plan['limits']}
                changed_servers.append({**server, 'attributes': attributes})
            if not found:
                continue
            estimate = balance_estimate(max(Decimal(0), remaining), changed_servers, products)
            estimate['affordable'] = remaining >= 0
            estimate['upfront_label'] = format_credits(upfront) if upfront >= Decimal('0.01') else f'{upfront:.4f}'
            estimates[str(plan['id'])] = estimate
        except (KeyError, TypeError, ValueError, InvalidOperation):
            continue
    return estimates
