"""Turn per-resource verdicts into the ordered FINDING → REMEDIATION → VERIFICATION plan."""
from .config import OPERATION_ORDER, PHASES
from .models import Step

RANK = {op: i for i, op in enumerate(OPERATION_ORDER)}


def plan(verdicts):
    """verdicts: [(system, resource_id, treatment, violated, evidence_id)] → (status, [Step])."""
    incomplete = any(v[3] for v in verdicts)
    steps = []
    for system, rid, treatment, violated, evidence in verdicts:
        if incomplete:
            if violated:
                steps.append(Step("FINDING", system, rid, *treatment.finding, evidence))
                steps.append(Step("REMEDIATION", system, rid, *treatment.remediation))
            steps.append(Step("VERIFICATION", system, rid, *treatment.verification))
        elif evidence:   # a COMPLETE verification must be backed by evidence
            steps.append(Step("VERIFICATION", system, rid, *treatment.verification, evidence))
    steps.sort(key=lambda s: (PHASES.index(s.phase), RANK[s.operation], s.system_id, s.resource_id))
    return ("INCOMPLETE" if incomplete else "COMPLETE"), steps


def to_json_steps(steps):
    return [{"step_no": i, "phase": s.phase, "system_id": s.system_id, "resource_id": s.resource_id,
             "operation": s.operation, "state": s.state, "evidence_id": s.evidence_id}
            for i, s in enumerate(steps, 1)]
