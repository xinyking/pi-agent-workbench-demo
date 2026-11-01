#!/usr/bin/env python3
"""Run isolated workbench evidence through DeepEval; dry-run by default, no paid calls."""
import argparse
from contextlib import nullcontext
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.deepeval_runner import read_profile, reserve_output, score_bundle
from evals.runner import load_case, run_suite


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['dry-run', 'offline', 'replay'], default='dry-run')
    parser.add_argument('--cases', default='C01,C02,R03,P01,P02,P03')
    parser.add_argument('--repeat', type=int, default=1)
    parser.add_argument('--fixture-variant', choices=['good', 'broken'], default='good')
    parser.add_argument('--timeout', type=float, default=20)
    parser.add_argument('--input', help='Existing frozen run_evals bundle; only read in replay mode')
    parser.add_argument('--output', help='New directory under artifacts/evals/')
    parser.add_argument('--judge', choices=['none', 'contract', 'local-http'], default='none')
    parser.add_argument('--judge-url', help='Explicit numeric loopback HTTP chat-completions URL')
    parser.add_argument('--judge-model')
    parser.add_argument('--judge-max-requests', type=int, default=100)
    parser.add_argument('--judge-timeout', type=float, default=15)
    args = parser.parse_args()
    names = args.cases.split(',')
    if not names or len(names) != len(set(names)):
        parser.error('cases must be nonempty and unique')
    for name in names:
        load_case(name)
    if not 1 <= args.repeat <= 20 or not 0 < args.timeout <= 300:
        parser.error('repeat must be 1–20 and timeout >0 and <=300')
    if not 1 <= args.judge_max_requests <= 10000 or not 0 < args.judge_timeout <= 60:
        parser.error('Invalid judge request budget/timeout')
    if args.mode == 'dry-run':
        print(json.dumps({'mode': 'dry-run', 'cases': names, 'profile': read_profile(),
                          'judge': args.judge, 'online_calls': False}, ensure_ascii=False, indent=2))
        return 0
    if not args.output:
        parser.error('--output is required')
    if (args.mode == 'replay') != bool(args.input):
        parser.error('--input is required only for replay')
    if args.judge == 'local-http' and not (args.judge_url and args.judge_model):
        parser.error('local-http requires --judge-url and --judge-model')
    if args.judge != 'local-http' and (args.judge_url or args.judge_model):
        parser.error('Judge URL/model only apply to local-http')
    # Optional dependencies are loaded only after explicit non-dry execution.
    from evals.deepeval_judge import LocalHTTPJudge, contract_judge_server
    context = contract_judge_server() if args.judge == 'contract' else nullcontext(args.judge_url)
    with context as url:
        judge = (LocalHTTPJudge(url, args.judge_model or 'synthetic-contract-not-quality',
                               args.judge_max_requests, args.judge_timeout)
                 if args.judge != 'none' else None)
        output = reserve_output(args.output)
        if args.mode == 'offline':
            input_dir = output / 'runtime'
            run_suite(names, input_dir, args.repeat, args.fixture_variant, args.timeout)
        else:
            input_dir = Path(args.input)
        summary = score_bundle(input_dir, output, args.judge, judge)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary['incomplete_trials']:
        return 2
    return 1 if summary['failed'] else 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, ImportError) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(2)
