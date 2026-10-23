"""Held-out acceptance checks run outside the candidate workspace."""
import json
import math
import runpy
import sys
from pathlib import Path


def grade(workspace, checks):
    results=[]
    for index,check in enumerate(checks):
        passed=False;observed=None
        try:
            function=runpy.run_path(str(workspace/check['file']))[check['function']]
            observed=function(*check['args'])
            expected=check.get('expected')
            passed='raises' not in check and (math.isclose(observed,expected,rel_tol=1e-9,abs_tol=1e-9) if type(observed) in (int,float) and type(expected) in (int,float) else observed==expected)
        except Exception as exc:
            observed=type(exc).__name__;passed=observed==check.get('raises')
        results.append({'id':f'holdout-{index+1}','passed':passed,'observed':observed})
    return results


if __name__=='__main__':
    payload=json.load(sys.stdin)
    print(json.dumps(grade(Path(sys.argv[1]),payload),ensure_ascii=False))
