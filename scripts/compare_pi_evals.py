#!/usr/bin/env python3
"""Compare DeepEval with the native deterministic judges used by pinned Pi."""
import argparse
import json
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evals.pi_comparison import fetch_reference, run_comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', action='store_true', help='Actually score; default only lists the plan')
    parser.add_argument('--fetch-reference', action='store_true', help='Fetch pinned public source only; no scoring')
    parser.add_argument('--positive')
    parser.add_argument('--negative')
    parser.add_argument('--reference-dir')
    parser.add_argument('--output')
    parser.add_argument('--node', default=shutil.which('node'))
    parser.add_argument('--no-controls', action='store_true')
    args = parser.parse_args()
    if args.fetch_reference:
        if args.run or not args.output:
            parser.error('--fetch-reference requires --output and cannot be combined with --run')
        print(json.dumps(fetch_reference(args.output), indent=2))
        return 0
    if not args.run:
        print(json.dumps({'mode': 'dry-run', 'scope': 'same-evidence-deterministic-judges-only',
                          'model_calls': False, 'pi_version': '1.3.0', 'vitest_evals_version': '0.15.0'}))
        return 0
    if not all([args.positive, args.negative, args.reference_dir, args.output, args.node]):
        parser.error('--run requires --positive, --negative, --reference-dir, --output and available --node')
    summary = run_comparison(args.positive, args.negative, args.reference_dir,
                             args.output, args.node, not args.no_controls)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, ImportError) as exc:
        print(f'{type(exc).__name__}: {exc}', file=sys.stderr)
        sys.exit(2)
