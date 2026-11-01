import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from workbench.ops.billing import DeepseekBilling, fetch_balance, official_base, task_estimate
from workbench.store import Store

PAYLOAD={'is_available':True,'balance_infos':[{'currency':'CNY','total_balance':'110.00','granted_balance':'10.00','topped_up_balance':'100.00'}]}

class BalanceTest(unittest.TestCase):
    def test_request_is_official_get_and_only_normalized_fields_returned(self):
        response=io.BytesIO(json.dumps(PAYLOAD|{'private':'discard'}).encode())
        opener=SimpleNamespace(open=lambda req,timeout: self.capture(req,timeout,response))
        with patch('workbench.ops.billing.request.build_opener',return_value=opener):
            result=fetch_balance('sk-test')
        self.assertEqual(result,PAYLOAD)
        self.assertEqual(self.req.full_url,'https://api.deepseek.com/user/balance')
        self.assertEqual(self.req.get_method(),'GET')
        self.assertEqual(self.req.get_header('Authorization'),'Bearer sk-test')
        self.assertEqual(self.timeout,8)
    def capture(self,req,timeout,response):
        self.req,self.timeout=req,timeout
        return response
    def test_redirect_auth_failure_and_bad_response_do_not_expose_key(self):
        for code in (302,401,429):
            opener=SimpleNamespace(open=lambda *a,**k: (_ for _ in ()).throw(HTTPError('https://api.deepseek.com',code,'sk-test',{},None)))
            with patch('workbench.ops.billing.request.build_opener',return_value=opener):
                with self.assertRaisesRegex(ValueError,f'HTTP {code}') as caught:fetch_balance('sk-test')
                self.assertNotIn('sk-test',str(caught.exception))
        for payload in [b'{}',b'not json',json.dumps({'is_available':True,'balance_infos':[{'currency':'CNY','total_balance':'NaN','granted_balance':'0','topped_up_balance':'0'}]}).encode(),b'x'*65537]:
            with patch('workbench.ops.billing.request.build_opener',return_value=SimpleNamespace(open=lambda *a,**k:io.BytesIO(payload))):
                with self.assertRaises(ValueError):fetch_balance('sk-test')
    def test_cache_connection_rotation_and_decimal_account_delta(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'PI_CODING_AGENT_DIR':tmp,'DEEPSEEK_API_KEY':''}):
            auth=Path(tmp)/'auth.json';auth.write_text(json.dumps({'deepseek':{'type':'api_key','key':'sk-test'}}))
            runtime=SimpleNamespace(store=SimpleNamespace(path=Path(tmp)/'state.sqlite3'),models=SimpleNamespace(read=lambda:{'providers':[]}))
            billing=DeepseekBilling(runtime)
            with patch('workbench.ops.billing.fetch_balance',return_value=PAYLOAD) as fetch,patch('workbench.ops.billing.time.time',return_value=1000):
                first=billing.refresh('pi-deepseek');billing.refresh('pi-deepseek');self.assertEqual(fetch.call_count,1)
            second=json.loads(json.dumps(PAYLOAD));second['balance_infos'][0]['total_balance']='109.90'
            with patch('workbench.ops.billing.fetch_balance',return_value=second),patch('workbench.ops.billing.time.time',return_value=1061):
                result=billing.refresh('pi-deepseek')
            self.assertEqual(result['sources'][0]['balance_change'][0]['net_decrease'],'0.10')
            self.assertIsNone(result['task_billed_amount'])
            self.assertNotIn('sk-test',json.dumps(result))
            self.assertNotIn('sk-test',billing.path.read_text())
            self.assertEqual(billing.path.stat().st_mode&0o777,0o600)
            auth.write_text(json.dumps({'deepseek':{'type':'api_key','key':'sk-new'}}))
            self.assertIsNone(billing.public()['sources'][0]['snapshot'])
    def test_official_connection_is_exact_not_proxy(self):
        self.assertTrue(official_base('https://api.deepseek.com/v1'))
        for url in ('http://api.deepseek.com','https://api.deepseek.com.attacker.test','https://user@api.deepseek.com','https://api.deepseek.com/?to=other'):
            self.assertFalse(official_base(url))

class EstimateTest(unittest.TestCase):
    def test_current_price_range_cache_math_and_unknown_legacy_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            store=Store(Path(tmp)/'state.sqlite3')
            try:
                rid=store.create('estimate')
                store.emit(rid,'usage',{'provider':'deepseek','model':'deepseek-v4-pro','prompt_tokens':1000,'cache_read_tokens':2000,'completion_tokens':300})
                store.emit(rid,'usage',{'model':'deepseek-v4-pro','prompt_tokens':1000,'completion_tokens':300})
                estimate=task_estimate(store,store.run(rid))
                self.assertEqual(estimate['minimum'],'0.00885');self.assertEqual(estimate['maximum'],'0.01770')
                self.assertEqual(estimate['priced_records'],1);self.assertEqual(estimate['unknown_records'],1)
                self.assertIsNone(estimate['billed_amount']);self.assertEqual(estimate['currency'],'CNY')
            finally:store.db.close()
