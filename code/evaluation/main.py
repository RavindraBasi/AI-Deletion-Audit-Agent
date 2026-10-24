"""Evaluate predictions without exposing sample labels to inference."""
import argparse
import csv
import json
from collections import Counter
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ghost_audit.contract import validate


def read(path):
    with Path(path).open(newline='') as f:
        rows=list(csv.DictReader(f))
    if len({r['case_id'] for r in rows}) != len(rows):
        raise ValueError('Duplicate case IDs')
    return {r['case_id']:r for r in rows}


def evaluate(truth, predictions):
    extra=set(predictions)-set(truth)
    if extra:
        raise ValueError(f'Unexpected case IDs: {sorted(extra)}')
    counts=Counter(); details=[]
    for cid,gold in truth.items():
        g=json.loads(gold['steps_json']); pred=predictions.get(cid)
        correct_status=bool(pred and pred['deletion_status']==gold['deletion_status'])
        p=json.loads(pred['steps_json']) if pred else []
        valid=True
        if pred:
            try: validate(pred)
            except (ValueError,KeyError,TypeError): valid=False
        else: valid=False
        exact=bool(valid and correct_status and p==g)
        gs=Counter(json.dumps(s,sort_keys=True) for s in g)
        ps=Counter(json.dumps(s,sort_keys=True) for s in p)
        counts.update(status=int(correct_status), exact=int(exact),valid=int(valid),
                      tp=sum((gs & ps).values()), gold_steps=len(g),pred_steps=len(p))
        # Positional field accuracy penalizes ordering/missing/extra steps, as the contract does.
        total=7*max(len(g),len(p)); hits=0
        for a,b in zip(g,p):
            hits+=sum(a.get(k)==b.get(k) for k in a)
        counts.update(field_hits=hits,field_total=total)
        details.append(dict(case_id=cid,status_correct=correct_status,exact_sequence=exact,
                            schema_valid=valid,expected_steps=len(g),predicted_steps=len(p)))
    n=len(truth)
    if not n: raise ValueError('Empty reference set')
    return dict(cases=n,coverage=len(predictions)/n,status_accuracy=counts['status']/n,
                exact_row_and_sequence_accuracy=counts['exact']/n,schema_valid_rate=counts['valid']/n,
                exact_step_precision=counts['tp']/counts['pred_steps'] if counts['pred_steps'] else None,
                exact_step_recall=counts['tp']/counts['gold_steps'] if counts['gold_steps'] else None,
                positional_field_accuracy=counts['field_hits']/counts['field_total'] if counts['field_total'] else None,
                details=details)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference',type=Path,required=True)
    parser.add_argument('--predictions',type=Path,required=True)
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--usage',type=Path)
    args=parser.parse_args()
    result=evaluate(read(args.reference),read(args.predictions))
    args.report.mkdir(parents=True,exist_ok=True)
    summary={k:v for k,v in result.items() if k!='details'}
    text='# Evaluation report\n\nReference: '+str(args.reference)+'\n\nMetrics describe this reference set only; hidden-set accuracy is unknown.\n\n'
    text+='\n'.join(f'- {k}: {v}' for k,v in summary.items())
    text+='\n\n## Per-case results\n\n```json\n'+json.dumps(result['details'],indent=2)+'\n```\n'
    text+='\n## Additional authored tests\n\nRun `python -m unittest discover -s code/tests -v`. Tests cover historical identity matching, future evidence exclusion, resource linking, and output validation. Record their actual outcome for the submitted version.\n\n## Failures and improvements\n\nReview every non-exact case above. Record investigated causes and measured improvements before submission; this generated report does not invent explanations.\n'
    (args.report/'evaluation_report.md').write_text(text)
    if args.usage:
        rows=json.loads(args.usage.read_text()); grouped={}
        for row in rows:
            name=row['model']; x=grouped.setdefault(name,Counter())
            x.update(cases=1,input_tokens=row['input_tokens'],output_tokens=row['output_tokens'],
                     newly_executed_calls=int(not row['cache_hit']),cache_hits=int(row['cache_hit']),
                     newly_executed_seconds=row['seconds'] if not row['cache_hit'] else 0)
        text='# Usage report\n\nLocal Hugging Face Transformers inference; no external API calls. External API cost: $0. Kaggle free allocation, no monetary compute valuation assigned.\n\n'
        text+='Scope: '+str(args.predictions)+'; '+str(len(rows))+' recorded inference results. A sample run does not satisfy the final 518-case usage requirement.\n\n'
        for name,x in grouped.items():
            text+='## '+name+'\n\n'+json.dumps(dict(x),indent=2)+'\n\n'
        ins=sum(r['input_tokens'] for r in rows); outs=sum(r['output_tokens'] for r in rows)
        text+=f'Overall input tokens: {ins}; output tokens: {outs}; total: {ins+outs}; average per recorded request: {(ins+outs)/len(rows) if rows else 0}. Estimated external API cost per request: $0. Cached tokens describe the logical run; cached calls consume no new inference tokens. Generated responses that fail parsing or schema validation remain included in these usage totals. Hardware/runtime failures before generation completes require separate diagnosis.\n'
        (args.report/'usage_report.md').write_text(text)
    print(json.dumps(summary,indent=2))


if __name__=='__main__': main()
