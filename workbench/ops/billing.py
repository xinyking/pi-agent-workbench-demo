"""DeepSeek official balance and explicitly separated current-price task estimates."""
import hashlib
import json
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from urllib import request, error, parse
from workbench.models.discovery import NoRedirect, validate_key

BALANCE_URL = 'https://api.deepseek.com/user/balance'
PRICE_URL = 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/'
PRICE_DATE = '2026-11-01'
# CNY / million tokens, off-peak; peak is twice this. Snapshot, not historical invoice.
RATES = {'deepseek-v4-pro': ('0.15', '4.5', '13.5'), 'deepseek-flash': ('0.02', '1', '4'),
         'deepseek-v4-flash': ('0.02', '1', '4'), 'deepseek-v4-flash-vision-exp': ('0.02', '1', '4')}


def official_base(base):
    try:
        url = parse.urlsplit(base)
        return url.scheme == 'https' and url.netloc == 'api.deepseek.com' and not url.query and not url.fragment and url.path.rstrip('/') in ('', '/v1', '/anthropic', '/beta')
    except (ValueError, TypeError):
        return False


def amount(value):
    try:
        if isinstance(value, bool):
            raise ValueError()
        result = Decimal(str(value))
        if not result.is_finite():
            raise ValueError()
        return result
    except (InvalidOperation, ValueError):
        raise ValueError('DeepSeek 返回了无效金额') from None


def fetch_balance(key):
    validate_key(key)
    opener = request.build_opener(request.ProxyHandler({}), NoRedirect())
    req = request.Request(BALANCE_URL, headers={'Authorization': 'Bearer ' + key, 'Accept': 'application/json'}, method='GET')
    try:
        with opener.open(req, timeout=8) as response:
            raw = response.read(65537)
            if len(raw) > 65536:
                raise ValueError('DeepSeek 余额响应超过大小限制')
        data = json.loads(raw)
        if type(data.get('is_available')) is not bool or not isinstance(data.get('balance_infos'), list) or not 1 <= len(data['balance_infos']) <= 2:
            raise ValueError('DeepSeek 余额响应格式无效')
        balances = []
        for row in data['balance_infos']:
            if row.get('currency') not in ('CNY', 'USD'):
                raise ValueError('DeepSeek 返回了未知币种')
            balances.append({field: format(amount(row[field]), 'f') for field in ('total_balance', 'granted_balance', 'topped_up_balance')} | {'currency': row['currency']})
        if len({row['currency'] for row in balances}) != len(balances):
            raise ValueError('DeepSeek 返回了重复币种')
        return {'is_available': data['is_available'], 'balance_infos': balances}
    except error.HTTPError as exc:
        code = exc.code
        exc.close()
        raise ValueError(f'DeepSeek 余额查询失败（HTTP {code}）；请核对官方 API Key 与账户权限') from None
    except (error.URLError, OSError, TimeoutError):
        raise ValueError('DeepSeek 余额查询连接失败或超时，请稍后重试') from None
    except (KeyError, TypeError, AttributeError, json.JSONDecodeError, UnicodeError):
        raise ValueError('DeepSeek 余额响应格式无效') from None


class DeepseekBilling:
    def __init__(self, runtime):
        self.runtime = runtime
        self.path = runtime.store.path.parent / 'deepseek-billing.json'
        self.lock = threading.RLock()

    def credentials(self):
        choices = []
        # Never execute Pi shell-based credential resolvers or copy OAuth tokens.
        auth_path = Path(os.environ.get('PI_CODING_AGENT_DIR', str(Path.home() / '.pi/agent'))) / 'auth.json'
        try:
            credential = json.loads(auth_path.read_text()).get('deepseek', {})
            key = credential.get('key', '') if credential.get('type') == 'api_key' else ''
            if key and not key.startswith('!'):
                key = os.environ.get(key, key)
                choices.append(('pi-deepseek', 'Pi 原生 DeepSeek', key))
        except (OSError, ValueError, AttributeError):
            pass
        if os.environ.get('DEEPSEEK_API_KEY'):
            choices.append(('env-deepseek', '环境变量 DeepSeek', os.environ['DEEPSEEK_API_KEY']))
        for provider in self.runtime.models.read()['providers']:
            if official_base(provider['base_url']) and provider.get('api_key'):
                choices.append(('wbapi-' + provider['id'], provider['name'], provider['api_key']))
        return choices

    def _cache(self):
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _identity(self, source, key):
        return hashlib.sha256((source + '\0' + key).encode()).hexdigest()

    def public(self):
        with self.lock:
            cache = self._cache()
            sources = []
            for ident, name, key in self.credentials():
                samples = cache.get(self._identity(ident, key), [])
                latest = samples[-1] if samples else None
                delta = []
                if len(samples) == 2:
                    previous = {r['currency']: r for r in samples[0]['balance_infos']}
                    for row in latest['balance_infos']:
                        if row['currency'] in previous:
                            delta.append({'currency': row['currency'], 'net_decrease': format(amount(previous[row['currency']]['total_balance']) - amount(row['total_balance']), 'f')})
                sources.append({'id': ident, 'name': name, 'snapshot': latest, 'previous_at': samples[0]['queried_at'] if len(samples) == 2 else None, 'balance_change': delta})
            return {'sources': sources, 'balance_source': BALANCE_URL, 'task_billed_amount': None, 'billing_note': '官方接口仅提供账户余额；未提供逐任务扣费。余额净减少含并发消费、充值、赠金变化，不能归因到单个任务。'}

    def refresh(self, source):
        with self.lock:
            choice = next((row for row in self.credentials() if row[0] == source), None)
            if choice is None:
                raise ValueError('未找到 DeepSeek 官方连接，请在模型设置配置官方 API Key')
            _, _, key = choice
            ident = self._identity(source, key)
            cache = self._cache()
            samples = cache.get(ident, [])
            if samples and time.time() - samples[-1]['queried_timestamp'] < 60:
                return self.public() | {'cached': True}
            result = fetch_balance(key)
            now = time.time()
            snapshot = {**result, 'queried_timestamp': now, 'queried_at': datetime.fromtimestamp(now, timezone.utc).isoformat()}
            cache[ident] = (samples + [snapshot])[-2:]
            # Only two sanitized official balance snapshots per configured credential.
            valid = {self._identity(i, k) for i, _, k in self.credentials()}
            cache = {i: value for i, value in cache.items() if i in valid}
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(mode='w', dir=self.path.parent, delete=False, prefix='.balance-') as stream:
                    temporary = Path(stream.name)
                    json.dump(cache, stream, ensure_ascii=False)
                temporary.chmod(0o600)
                os.replace(temporary, self.path)
            finally:
                if temporary:
                    temporary.unlink(missing_ok=True)
            return self.public() | {'cached': False}


def task_estimate(store, run, official_providers=()):
    with store.lock:
        rows = store.db.execute("SELECT data FROM events WHERE run_id=? AND kind='usage' ORDER BY id", (run['id'],)).fetchall()
    low, high, known = Decimal(0), Decimal(0), 0
    for row in rows:
        d = json.loads(row['data'])
        model = d.get('model', '').split('/')[-1]
        if d.get('provider') not in ('deepseek', *official_providers) or model not in RATES:
            continue
        fields = ('prompt_tokens', 'cache_read_tokens', 'completion_tokens')
        if not all(type(d.get(k)) in (int, float) and d[k] >= 0 for k in fields) or d.get('cache_write_tokens', 0):
            continue
        try:
            miss, hit, output = (amount(d[k]) for k in fields)
        except ValueError:
            continue
        rate_hit, rate_miss, rate_output = map(Decimal, RATES[model])
        minimum = (miss * rate_miss + hit * rate_hit + output * rate_output) / 1000000
        low += minimum
        high += minimum * 2
        known += 1
    return {'currency': 'CNY', 'minimum': str(low) if known else None, 'maximum': str(high) if known else None, 'priced_records': known, 'unknown_records': len(rows) - known, 'price_date': PRICE_DATE, 'price_source': PRICE_URL, 'basis': '按当前官方价格快照重算；区间覆盖空闲/高峰单价，不代表历史实际扣费；不含未采集的 Search/Fetch 等额外费用', 'billed_amount': None}
