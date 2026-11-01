"""Usage evidence from Pi; USD amounts are model rate estimates, not invoices."""
import math


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else 0


def pi_usage(message, fallback_model=''):
    usage = message.get('usage') or {}
    provider = message.get('provider', '')
    model = message.get('model') or fallback_model
    cost = usage.get('cost') or {}
    total = cost.get('total')
    # Managed providers currently register zero placeholder rates. Zero alone
    # cannot establish free service, including missing prices in native models.
    known = (not provider.startswith('wbapi-') and not model.startswith('wbapi-')
             and type(total) in (int, float) and math.isfinite(total) and total > 0)
    return {'provider': provider, 'model': model, 'source': 'pi',
            'prompt_tokens': number(usage.get('input')), 'completion_tokens': number(usage.get('output')),
            'cache_read_tokens': number(usage.get('cacheRead')), 'cache_write_tokens': number(usage.get('cacheWrite')),
            'total_tokens': number(usage.get('totalTokens')),
            'cost_usd': total if known else None, 'cost_source': 'pi_model_rates' if known else 'unknown',
            'stop_reason': message.get('stopReason')}


def summarize(records):
    result = dict.fromkeys(('prompt_tokens', 'completion_tokens', 'cache_read_tokens', 'cache_write_tokens', 'total_tokens'), 0)
    result.update(requests=0, cost_usd=0, cost_known_requests=0, cost_unknown_requests=0)
    for data in records:
        result['requests'] += 1
        for key in ('prompt_tokens', 'completion_tokens', 'cache_read_tokens', 'cache_write_tokens', 'total_tokens'):
            result[key] += number(data.get(key))
        cost = data.get('cost_usd')
        if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
            result['cost_usd'] += cost
            result['cost_known_requests'] += 1
        else:
            result['cost_unknown_requests'] += 1
    return result
