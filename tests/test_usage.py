import unittest
from workbench.ops.usage import pi_usage, summarize


class UsageTests(unittest.TestCase):
    def test_unpriced_custom_and_zero_are_unknown(self):
        for provider, model, cost in [('wbapi-local','m',.1), ('','wbapi-local/m',.1), ('native','m',0), ('native','m',None)]:
            data = pi_usage({'provider':provider,'model':model,'usage':{'cost':{'total':cost}}})
            self.assertIsNone(data['cost_usd'])
            self.assertEqual(data['cost_source'], 'unknown')

    def test_old_unknown_records_do_not_become_free_or_inflate_known_subtotal(self):
        data = pi_usage({'provider':'native','model':'m','usage':{'input':2,'output':3,'cacheRead':5,'cacheWrite':7,'totalTokens':17,'cost':{'total':.02}}})
        summary = summarize([data, {'prompt_tokens':1,'total_tokens':1}])
        self.assertEqual(summary['total_tokens'],18)
        self.assertEqual(summary['cache_read_tokens'],5)
        self.assertEqual(summary['cache_write_tokens'],7)
        self.assertEqual(summary['cost_usd'],.02)
        self.assertEqual(summary['cost_known_requests'],1)
        self.assertEqual(summary['cost_unknown_requests'],1)

    def test_nonfinite_and_negative_values_do_not_poison_totals(self):
        for invalid in (float('nan'),float('inf'),-1,True,'2'):
            summary=summarize([{'cost_usd':invalid,'total_tokens':invalid}])
            self.assertEqual(summary['cost_unknown_requests'],1)
            self.assertEqual(summary['total_tokens'],0)
