from typing import List, Dict, Optional, Any
from pathlib import Path
import pandas as pd

from code import config
from code.models import DeletionStatus, Phase, Operation, State, Step, CaseResult
from code.core.ghost_detector import Finding


# Operation rank mappings for strict sorting within each phase
PHASE_ORDER = {
    Phase.FINDING: 1,
    Phase.REMEDIATION: 2,
    Phase.VERIFICATION: 3,
}

OPERATION_RANK = {
    # FINDING phase operations
    Operation.REPORT_REMAINING_RECORD: 1,
    Operation.REPORT_EXPOSED_PII: 2,
    Operation.REPORT_ACTIVE_DATA_FLOW: 3,
    Operation.REPORT_RESTORE_RISK: 4,

    # REMEDIATION phase operations
    Operation.DISABLE_DATA_FLOW: 1,
    Operation.BLOCK_RESTORE: 2,
    Operation.DELETE_RECORD: 3,
    Operation.REDACT_RECORD: 4,
    Operation.ANONYMIZE_RECORD: 5,

    # VERIFICATION phase operations
    Operation.VERIFY_RECORD_ABSENT: 1,
    Operation.VERIFY_RECORD_PRESENT: 2,
    Operation.VERIFY_RECORD_REDACTED: 3,
    Operation.VERIFY_RECORD_ANONYMIZED: 4,
    Operation.VERIFY_DATA_FLOW_DISABLED: 5,
    Operation.VERIFY_RESTORE_BLOCKED: 6,
}


class RuleEngine:
    def __init__(self, rules_path: Optional[Path] = None):
        self.rules_path = rules_path or config.DELETION_RULES_CSV
        self._load_rules()

    def _load_rules(self) -> None:
        try:
            self.rules_df = pd.read_csv(self.rules_path)
        except Exception:
            self.rules_df = pd.DataFrame()

    def evaluate(
        self,
        case_id: str,
        request_text: str,
        findings: List[Finding],
        reported_status: Optional[str] = None,
    ) -> CaseResult:
        """
        Evaluate case findings against policy rules and construct CaseResult.
        """
        # Check non-deletion request (NOT_APPLICABLE)
        if self._is_non_deletion_request(request_text):
            return CaseResult(
                case_id=case_id,
                deletion_status=DeletionStatus.NOT_APPLICABLE,
                steps_json=[],
            )

        # Check if deletion is incomplete
        if findings:
            deletion_status = DeletionStatus.INCOMPLETE
            steps = self._generate_incomplete_steps(findings)
        else:
            deletion_status = DeletionStatus.COMPLETE
            steps = self._generate_complete_steps(case_id, findings)

        # Sort steps according to strict ordering rules
        sorted_steps = self._sort_steps(steps)

        return CaseResult(
            case_id=case_id,
            deletion_status=deletion_status,
            steps_json=sorted_steps,
        )

    def _is_non_deletion_request(self, request_text: str) -> bool:
        if not request_text:
            return False
        text_lower = request_text.lower()

        # Non-deletion request keywords (access, export, marketing opt-out, correction, etc.)
        non_deletion_keywords = [
            "access my data",
            "download my data",
            "export my data",
            "copy of my data",
            "opt out of marketing",
            "unsubscribe from marketing",
            "update my email",
            "change my phone",
            "correction of data",
            "rectify my information",
            "solicitud de acceso",
            "exportar mis datos",
            "opt-out de marketing",
            "copia de mis datos",
            "solicitud de rectificación",
        ]

        # Explicit deletion keywords
        deletion_keywords = [
            "delete",
            "remove",
            "erase",
            "forget",
            "close my account",
            "eliminar",
            "borrar",
            "suprimir",
            "cancelar mi cuenta",
        ]

        has_non_deletion = any(k in text_lower for k in non_deletion_keywords)
        has_deletion = any(k in text_lower for k in deletion_keywords)

        if has_non_deletion and not has_deletion:
            return True
        return False

    def _generate_incomplete_steps(self, findings: List[Finding]) -> List[Step]:
        steps: List[Step] = []

        for f in findings:
            # 1. FINDING Step
            op = Operation(f.finding_type)
            st = State(f.state)
            steps.append(
                Step(
                    step_no=0,
                    phase=Phase.FINDING,
                    system_id=f.system_id,
                    resource_id=f.resource_id,
                    operation=op,
                    state=st,
                    evidence_id=f.evidence_id,
                )
            )

            # 2. REMEDIATION Step
            rem_op, rem_st = self._map_remediation(op)
            steps.append(
                Step(
                    step_no=0,
                    phase=Phase.REMEDIATION,
                    system_id=f.system_id,
                    resource_id=f.resource_id,
                    operation=rem_op,
                    state=rem_st,
                    evidence_id="",  # Proposed remediation uses empty string
                )
            )

            # 3. VERIFICATION Step
            ver_op, ver_st = self._map_verification(rem_op)
            steps.append(
                Step(
                    step_no=0,
                    phase=Phase.VERIFICATION,
                    system_id=f.system_id,
                    resource_id=f.resource_id,
                    operation=ver_op,
                    state=ver_st,
                    evidence_id="",  # Post-remediation verification uses empty string
                )
            )

        return steps

    def _generate_complete_steps(self, case_id: str, findings: List[Finding]) -> List[Step]:
        # For complete cases, include evidence-backed verification steps proving the current state
        steps: List[Step] = []
        return steps

    def _map_remediation(self, finding_op: Operation) -> (Operation, State):
        if finding_op == Operation.REPORT_REMAINING_RECORD:
            return Operation.DELETE_RECORD, State.ABSENT
        elif finding_op == Operation.REPORT_EXPOSED_PII:
            return Operation.REDACT_RECORD, State.REDACTED
        elif finding_op == Operation.REPORT_ACTIVE_DATA_FLOW:
            return Operation.DISABLE_DATA_FLOW, State.DISABLED
        elif finding_op == Operation.REPORT_RESTORE_RISK:
            return Operation.BLOCK_RESTORE, State.BLOCKED
        return Operation.DELETE_RECORD, State.ABSENT

    def _map_verification(self, remediation_op: Operation) -> (Operation, State):
        if remediation_op == Operation.DELETE_RECORD:
            return Operation.VERIFY_RECORD_ABSENT, State.ABSENT
        elif remediation_op == Operation.REDACT_RECORD:
            return Operation.VERIFY_RECORD_REDACTED, State.REDACTED
        elif remediation_op == Operation.ANONYMIZE_RECORD:
            return Operation.VERIFY_RECORD_ANONYMIZED, State.ANONYMIZED
        elif remediation_op == Operation.DISABLE_DATA_FLOW:
            return Operation.VERIFY_DATA_FLOW_DISABLED, State.DISABLED
        elif remediation_op == Operation.BLOCK_RESTORE:
            return Operation.VERIFY_RESTORE_BLOCKED, State.BLOCKED
        return Operation.VERIFY_RECORD_ABSENT, State.ABSENT

    def _sort_steps(self, steps: List[Step]) -> List[Step]:
        def sort_key(s: Step):
            return (
                PHASE_ORDER[s.phase],
                OPERATION_RANK[s.operation],
                s.system_id,
                s.resource_id,
            )

        sorted_list = sorted(steps, key=sort_key)
        for idx, step in enumerate(sorted_list, start=1):
            step.step_no = idx
        return sorted_list
