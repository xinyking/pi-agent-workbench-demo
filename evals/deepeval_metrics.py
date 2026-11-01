"""Real DeepEval metrics. No service hooks and no implicit provider credentials."""
import json
import os

# Set before the first DeepEval import. Never load .env or export traces/telemetry.
os.environ['DEEPEVAL_TELEMETRY_OPT_OUT'] = 'YES'
os.environ['DEEPEVAL_DISABLE_DOTENV'] = '1'
os.environ['DEEPEVAL_FILE_SYSTEM'] = 'READ_ONLY'
os.environ['DEEPEVAL_UPDATE_WARNING_OPT_IN'] = '0'
os.environ['CONFIDENT_TRACE_SAMPLE_RATE'] = '0'

from deepeval.metrics import BaseMetric, FaithfulnessMetric, GEval, ToolCorrectnessMetric
from deepeval.models import DeepEvalBaseLLM
from deepeval.test_case import LLMTestCase, SingleTurnParams, ToolCall, ToolCallParams


class NoCallsModel(DeepEvalBaseLLM):
    """Required by DeepEval's constructor; deterministic checks must never call it."""
    def load_model(self):
        return self

    def get_model_name(self):
        return 'no-model-deterministic-only'

    def generate(self, *args, **kwargs):
        raise RuntimeError('Unexpected judge call during deterministic evaluation')

    async def a_generate(self, *args, **kwargs):
        return self.generate(*args, **kwargs)


class WorkbenchChecksMetric(BaseMetric):
    def __init__(self, name, checks):
        self.name = name
        self.checks = checks
        self.threshold = 1.0
        self.async_mode = False
        self.evaluation_model = 'independent-business-checks'

    def measure(self, test_case, **kwargs):
        if not self.checks:
            raise ValueError('No independent evidence for applicable check group')
        self.score = sum(c['passed'] for c in self.checks) / len(self.checks)
        self.success = self.score == 1.0
        self.reason = '; '.join(f"{c['id']}={'pass' if c['passed'] else 'FAIL'}" for c in self.checks)
        return self.score

    async def a_measure(self, test_case, **kwargs):
        return self.measure(test_case, **kwargs)

    def is_successful(self):
        return self.success

    @property
    def __name__(self):
        return self.name


def make_test_case(sample):
    return LLMTestCase(
        input=sample['input'], actual_output=sample['actual_output'],
        tools_called=[ToolCall(name=t['name'], input_parameters=t['input_parameters']) for t in sample['tools']],
        expected_tools=[ToolCall(**t) for t in sample['expected_tools']],
        retrieval_context=sample['retrieval_context'] or None,
        # Final artifacts and independent observations are evidence, not the candidate's self-rating.
        context=[json.dumps({'artifacts': sample['artifacts'], 'checks': sample['checks']},
                            ensure_ascii=False)],
    )


def metric_plan(sample, profile, judge=None):
    thresholds = profile['thresholds']
    checks = sample['checks']
    metrics = [('business_checks', WorkbenchChecksMetric('Business checks', checks))]
    metrics.append(('tool_contract', ToolCorrectnessMetric(
        model=NoCallsModel(), threshold=thresholds['tools'], async_mode=False,
        evaluation_params=[ToolCallParams.INPUT_PARAMETERS],
        should_exact_match=False, should_consider_ordering=False, eval_mode='llm')))
    groups = {
        'P01': ('citation_checks', ('fact-', 'source-', 'exact-claim-', 'three-pages-')),
        'P02': ('safety_checks', ('no-sensitive-', 'no-canary-', 'no-unapproved-')),
        'P03': ('constraint_checks', ('constraint-', 'cross-task-')),
        'R03': ('recovery_checks', ('archive-', 'retry-', 'delivery-')),
    }
    if sample['case_id'] in groups:
        key, prefixes = groups[sample['case_id']]
        metrics.append((key, WorkbenchChecksMetric(key, [c for c in checks if c['id'].startswith(prefixes)])))
    if judge is not None:
        metrics.append(('delivery_quality', GEval(
            name='Evidence-backed delivery', model=judge, threshold=thresholds['quality'], async_mode=False,
            evaluation_steps=profile['quality_steps'],
            evaluation_params=[SingleTurnParams.INPUT, SingleTurnParams.ACTUAL_OUTPUT,
                               SingleTurnParams.CONTEXT])))
        if sample['case_id'] in ('P01', 'P02') and sample['retrieval_context']:
            metrics.append(('faithfulness', FaithfulnessMetric(
                model=judge, threshold=thresholds['faithfulness'], async_mode=False, eval_mode='llm')))
    return metrics
