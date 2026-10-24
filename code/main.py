"""Run CPU preparation, or offline local model inference. See code/KAGGLE.md."""
import argparse
import csv
import json
from pathlib import Path
from ghost_audit.pipeline import EvidenceIndex, read_csv
from ghost_audit.contract import validate


def main():
    root = Path(__file__).resolve().parents[1]
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',type=Path,default=root/'dataset')
    p.add_argument('--requests',default='sample_deletion_requests.csv')
    p.add_argument('--artifacts',type=Path,default=root/'artifacts')
    p.add_argument('--output',type=Path,default=root/'output.csv')
    p.add_argument('--model-path',type=Path)
    p.add_argument('--quantize',action='store_true')
    p.add_argument('--allow-extraction-gaps',action='store_true',help='Experimental partial-evidence run; never treat as full coverage')
    p.add_argument('--max-input-tokens',type=int,default=12000)
    args = p.parse_args()
    args.artifacts.mkdir(parents=True,exist_ok=True)
    index = EvidenceIndex(args.dataset,args.artifacts/'extraction_cache')
    requests = read_csv(args.dataset/args.requests)
    ids = [r['case_id'] for r in requests]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate request IDs')
    packets = [index.packet(r) for r in sorted(requests,key=lambda r:r['case_id'])]
    (args.artifacts/'coverage.json').write_text(json.dumps(index.coverage,indent=2))
    with (args.artifacts/'packets.jsonl').open('w') as f:
        for packet in packets:
            f.write(json.dumps(packet,ensure_ascii=False)+'\n')
    print(f'Prepared {len(packets)} cases, {len(index.units)} evidence units')
    gaps = [r for r in index.coverage if r['status'] != 'extracted']
    print(f'Extraction gaps: {len(gaps)}; details in coverage.json')
    if not args.model_path:
        print('CPU preparation complete. No predictions generated; supply --model-path for offline inference.')
        return
    if gaps and not args.allow_extraction_gaps:
        raise ValueError('Resolve extraction gaps before inference, or explicitly opt into a partial-evidence experiment')
    from ghost_audit.local_model import LocalModel
    model = LocalModel(args.model_path,args.artifacts/'model_cache',args.max_input_tokens,quantize=args.quantize)
    outputs, failures, usage = [], [], []
    for packet in packets:
        cid = packet['request']['case_id']
        try:
            result = model.predict(packet)
            usage.append({'case_id':cid,**{k:v for k,v in result.items() if k != 'raw_text'}})
            row = json.loads(result['raw_text'])
            if row.get('case_id') != cid:
                raise ValueError('Model returned wrong case ID or abstained: '+str(row.get('error','')))
            allowed = {u['evidence_id'] for u in packet['evidence']} | {u['audit_id'] for u in packet['identity_history']}
            steps = validate(row,allowed)
            row['steps_json'] = json.dumps(steps,ensure_ascii=False,separators=(',',':'))
            outputs.append(row)
        except (ValueError,KeyError,TypeError) as exc:
            failures.append({'case_id':cid,'error':str(exc)})
    (args.artifacts/'usage.json').write_text(json.dumps(usage,indent=2))
    (args.artifacts/'failures.json').write_text(json.dumps(failures,indent=2))
    with (args.artifacts/'partial_predictions.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['case_id','deletion_status','steps_json']); writer.writeheader(); writer.writerows(outputs)
    if failures:
        raise SystemExit(f'{len(failures)} cases failed. Partial results saved for diagnosis; final output was not written.')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    tmp = args.output.with_suffix('.tmp')
    with tmp.open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=['case_id','deletion_status','steps_json']); writer.writeheader(); writer.writerows(outputs)
    tmp.replace(args.output)
    print(f'Wrote {len(outputs)} validated rows to {args.output}')


if __name__ == '__main__':
    main()
