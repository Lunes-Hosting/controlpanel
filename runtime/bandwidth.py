"""Validate optional plan policies and translate the addon's two API formats."""
from copy import deepcopy

POLICY_FIELDS = ('bandwidth_gb', 'network_speed_mbps', 'bandwidth_overage_action',
                 'bandwidth_overage_mbps', 'bandwidth_custom')


def _integer(value, name, maximum, minimum=0):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer from {minimum} to {maximum}.')
    return value


def plan_bandwidth(product):
    limits = product.get('limits') or {}
    allowance = limits.get('bandwidth_gb')
    allowance = _integer(0 if allowance is None else allowance, 'bandwidth_gb', 1000000)
    policy = dict(bandwidth_limit_gb=allowance, bandwidth_speed_mbps=0,
                  bandwidth_overage_action='block', bandwidth_overage_mbps=1,
                  bandwidth_custom=True)
    if allowance == 0:
        # Explicit unlimited overrides fleet defaults; never inherit silently.
        return policy
    policy['bandwidth_speed_mbps'] = _integer(limits.get('network_speed_mbps', 0), 'network_speed_mbps', 100000)
    action = limits.get('bandwidth_overage_action', 'block')
    if action == 'blocked':
        action = 'block'
    if action not in ('block', 'throttle'):
        raise ValueError('bandwidth_overage_action must be block or throttle.')
    policy['bandwidth_overage_action'] = action
    if action == 'throttle':
        policy['bandwidth_overage_mbps'] = _integer(
            limits.get('bandwidth_overage_mbps', 1), 'bandwidth_overage_mbps', 100000, 1)
    return policy


def resource_limits(product):
    return {key: deepcopy(value) for key, value in product['limits'].items() if key not in POLICY_FIELDS}


def creation_limits(product):
    policy = plan_bandwidth(product)
    limits = resource_limits(product)
    limits.update(bandwidth_gb=policy['bandwidth_limit_gb'], network_speed_mbps=policy['bandwidth_speed_mbps'],
                  bandwidth_overage_action=policy['bandwidth_overage_action'],
                  bandwidth_overage_mbps=policy['bandwidth_overage_mbps'], bandwidth_custom=True)
    return limits


def bandwidth_view(data):
    """Display effective policy, never infer an existing server's plan by RAM."""
    effective = data['effective']
    limit = _integer(effective['limit_gb'], 'limit_gb', 1000000)
    speed = _integer(effective['speed_mbps'], 'speed_mbps', 100000)
    action = effective['overage_action']
    if action not in ('block', 'throttle'):
        raise ValueError('Invalid effective policy.')
    overage = _integer(effective['overage_mbps'], 'overage_mbps', 100000, 1)
    usage = data.get('usage') or {}
    used_bytes = usage.get('used_bytes', 0)
    used_bytes = _integer(used_bytes, 'used_bytes', 2**64 - 1)
    return dict(limit_gb=limit, speed_mbps=speed, overage_action=action, overage_mbps=overage,
                used_gb=round(used_bytes / 1000000000, 2),
                exhausted=bool(limit and used_bytes >= limit * 1000000000),
                reset_at=str(usage.get('reset_at') or '')[:40])
