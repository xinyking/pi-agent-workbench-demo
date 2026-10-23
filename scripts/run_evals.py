#!/usr/bin/env python3
"""Run isolated, deterministic Agent evaluation cases; never online by default."""
import argparse
import json
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from evals.runner import load_case,run_suite


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['dry-run','offline','online'],default='dry-run')
    parser.add_argument('--cases',default='C01,C02,R03');parser.add_argument('--repeat',type=int,default=1)
    parser.add_argument('--fixture-variant',choices=['good','broken'],default='good')
    parser.add_argument('--output');parser.add_argument('--timeout',type=float,default=20)
    args=parser.parse_args();names=args.cases.split(',')
    if args.mode=='online':parser.error('在线适配尚未实现；需要模型、价格快照、总预算与独立授权，不会发起在线请求')
    if not 0<args.timeout<=300:parser.error('timeout 需要在 0–300 秒内')
    if args.mode=='dry-run':print(json.dumps({'mode':'dry-run','cases':[load_case(name)['id'] for name in names],'online_calls':False},ensure_ascii=False));return 0
    if not args.output:parser.error('offline 需要 --output artifacts/evals/<新目录>')
    summary=run_suite(names,args.output,args.repeat,args.fixture_variant,args.timeout)
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    if summary['blocked']:return 2
    return 0 if summary['failed']==0 and summary['source_unchanged'] else 1


if __name__=='__main__':
    try:sys.exit(main())
    except (ValueError,OSError) as exc:print(str(exc),file=sys.stderr);sys.exit(2)
