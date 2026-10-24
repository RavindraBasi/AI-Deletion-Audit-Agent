"""Submission schema and deterministic ordering shared by inference/evaluation."""
import json

OPS = {
 'FINDING': [('REPORT_REMAINING_RECORD','PRESENT'),('REPORT_EXPOSED_PII','PRESENT'),('REPORT_ACTIVE_DATA_FLOW','ACTIVE'),('REPORT_RESTORE_RISK','ACTIVE')],
 'REMEDIATION': [('DISABLE_DATA_FLOW','DISABLED'),('BLOCK_RESTORE','BLOCKED'),('DELETE_RECORD','ABSENT'),('REDACT_RECORD','REDACTED'),('ANONYMIZE_RECORD','ANONYMIZED')],
 'VERIFICATION': [('VERIFY_RECORD_ABSENT','ABSENT'),('VERIFY_RECORD_PRESENT','PRESENT'),('VERIFY_RECORD_REDACTED','REDACTED'),('VERIFY_RECORD_ANONYMIZED','ANONYMIZED'),('VERIFY_DATA_FLOW_DISABLED','DISABLED'),('VERIFY_RESTORE_BLOCKED','BLOCKED')]
}
FIELDS = {'step_no','phase','system_id','resource_id','operation','state','evidence_id'}


def step_key(s):
    return (list(OPS).index(s['phase']), [o for o,_ in OPS[s['phase']]].index(s['operation']), s['system_id'], s['resource_id'])


def validate(row, allowed_evidence=None):
    if set(row) != {'case_id','deletion_status','steps_json'}:
        raise ValueError('Incorrect output fields')
    steps = json.loads(row['steps_json']) if isinstance(row['steps_json'], str) else row['steps_json']
    status = row['deletion_status']
    if status not in {'COMPLETE','INCOMPLETE','NOT_APPLICABLE'} or not isinstance(steps,list):
        raise ValueError('Invalid status or steps')
    if status == 'NOT_APPLICABLE' and steps:
        raise ValueError('Non-deletion must have no steps')
    if status == 'COMPLETE' and not steps:
        raise ValueError('Complete requires evidence-backed verification')
    if status == 'INCOMPLETE' and {s.get('phase') for s in steps} != set(OPS):
        raise ValueError('Incomplete requires all three phases')
    for n,s in enumerate(steps,1):
        if set(s) != FIELDS or type(s['step_no']) is not int or s['step_no'] != n:
            raise ValueError('Invalid fields or numbering')
        if any(not isinstance(s[k], str) for k in FIELDS - {'step_no'}):
            raise ValueError('Step values must be strings')
        if not s['system_id'] or not s['resource_id']:
            raise ValueError('Empty resource or system')
        if (s['operation'],s['state']) not in OPS.get(s['phase'],[]):
            raise ValueError('Invalid phase/operation/state')
        cite = status == 'COMPLETE' or s['phase'] == 'FINDING'
        if cite and (not s['evidence_id'] or (allowed_evidence is not None and s['evidence_id'] not in allowed_evidence)):
            raise ValueError('Missing or unavailable evidence citation')
        if not cite and s['evidence_id']:
            raise ValueError('Future steps must not cite evidence')
        if status == 'COMPLETE' and s['phase'] != 'VERIFICATION':
            raise ValueError('Complete permits verification only')
    if steps != sorted(steps,key=step_key):
        raise ValueError('Incorrect step order')
    return steps
