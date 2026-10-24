"""audit_case(case, ctx) -> CaseResult: the per-case decision procedure of SPEC §5."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from ..evidence.records import system_for
from ..evidence.store import ObservationStore
from ..identity import IdentityResolver, Subject
from ..intent import DELETE, IntentLabel
from ..models import Activity, Case, CaseResult, Kind, Observation, Step
from .linking import Linker, assign_workflows
from .planner import ResourcePlan, build_steps
from .policy import Policy
from .state import ResourceState, reconstruct

# Systems whose records only need an existence check when inferring absence from a full snapshot.
IMPLICIT_ABSENCE_TREATMENTS = {"DELETE"}
# A record last seen this long before the request belongs to an earlier lifecycle (e.g. an earlier request of
# the same person); it cannot establish anything about this request.
STALE_BEFORE_REQUEST = timedelta(days=90)


@dataclass
class AuditContext:
    store: ObservationStore
    resolver: IdentityResolver
    policy: Policy
    linker: Linker
    intents: dict[str, IntentLabel]
    workflows: dict[str, Activity | None] = field(default_factory=dict)
    subjects: dict[str, Subject] = field(default_factory=dict)
    direct: dict[str, dict] = field(default_factory=dict)

    def prepare(self, cases: list[Case]) -> None:
        """Resolve subjects and assign deletion workflows across every known request at once."""
        items = []
        for c in cases:
            s = self.resolver.resolve(c.email, c.requested_at) or self.resolver.bare_subject(c.email)
            self.subjects[c.case_id] = s
            self.direct[c.case_id] = self.linker.direct_resources(s, c.audit_at)
            items.append((c, s, self.direct[c.case_id]))
        self._items = items
        self._by_cutoff: dict = {}

    def workflow_for(self, case: Case) -> Activity | None:
        """The deletion workflow of `case`, from an assignment computed with information up to its audit_at."""
        key = case.audit_at
        if key not in self._by_cutoff:
            self._by_cutoff[key] = assign_workflows(self.linker, self._items, cutoff=key)
        return self._by_cutoff[key].get(case.case_id)


def _implicit_absences(store: ObservationStore, system_id: str, resource_id: str, audit_at: datetime) -> list[Observation]:
    out = []
    for cov in store.coverage:
        if cov.system_id == system_id and cov.captured_at <= audit_at and resource_id not in cov.present:
            out.append(Observation(cov.evidence_id, cov.captured_at, cov.captured_at, system_id, resource_id,
                                   Kind.ABSENT, None, frozenset(), "snapshot", {}, explicit=False))
    return out


def _last_seen(observations: list[Observation], audit_at: datetime) -> datetime:
    seen = [o.captured_at for o in observations if o.captured_at <= audit_at]
    return max(seen) if seen else datetime.min.replace(tzinfo=audit_at.tzinfo)


def _category(observations: list[Observation], audit_at: datetime) -> str | None:
    cats = [o.category for o in observations if o.captured_at <= audit_at and o.category]
    if "SUPPORT_ATTACHMENT" in cats:
        return "SUPPORT_ATTACHMENT"
    if "MARKETING_PROFILE" in cats:
        return "MARKETING_PROFILE"
    for c in cats:
        if c not in {"DATA_FLOW"}:
            return c
    return None


def audit_case(case: Case, ctx: AuditContext) -> CaseResult:
    label = ctx.intents.get(case.request_text)
    trace: dict = {"case_id": case.case_id, "intent": label.__dict__ if label else None}
    if label is not None and label.label != DELETE:
        return CaseResult(case.case_id, "NOT_APPLICABLE", [], trace)

    subject = ctx.subjects.get(case.case_id) or ctx.resolver.resolve(case.email, case.requested_at) \
        or ctx.resolver.bare_subject(case.email)
    direct = ctx.direct.get(case.case_id) or ctx.linker.direct_resources(subject, case.audit_at)
    trace["subject"] = {"user_id": subject.user_id, "identifiers": sorted(f"{k}:{v}" for k, v in subject.identifiers)}

    owned: dict[str, str] = {rid: "identifier" for rid in direct}
    workflow = ctx.workflow_for(case)
    account_id = None
    if workflow is not None:
        account_id = workflow.resource_id
        owned.setdefault(account_id, "workflow")
        for st in ctx.linker.workflow_steps(workflow, subject, case.audit_at):
            owned.setdefault(st.resource_id, "workflow")
    trace["workflow"] = workflow.activity_id if workflow else None

    plans: list[ResourcePlan] = []
    for rid in sorted(owned):
        system_id = system_for(rid)
        if system_id == "unknown":
            continue
        observations = ctx.store.by_resource(rid)
        category = _category(observations, case.audit_at)
        state = reconstruct(observations, case.audit_at)
        decision = ctx.policy.decide(system_id, category, state.attrs, case.audit_at)
        if decision.treatment in IMPLICIT_ABSENCE_TREATMENTS and state.kind is None:
            # absence inferred from a full snapshot is only a fallback when nothing explicit observed the record
            state = reconstruct(observations + _implicit_absences(ctx.store, system_id, rid, case.audit_at), case.audit_at)
        if system_id == "account_service":
            # one primary profile per request: other directly linked accounts only matter if still present
            if account_id and rid != account_id and state.kind != Kind.PRESENT:
                continue
            if state.kind is None:
                state = ResourceState(Kind.ABSENT, workflow.activity_id if workflow and rid == account_id else "")
        if state.kind is None:
            continue
        if system_id != "account_service" and _last_seen(observations, case.audit_at) < case.requested_at - STALE_BEFORE_REQUEST:
            continue
        state.category = category
        plans.append(ResourcePlan(system_id, rid, decision, state, owned[rid]))

    if not plans:
        return _no_resource(case, ctx, subject, direct, trace)

    status, steps = build_steps(plans)
    trace["resources"] = [
        {"system_id": p.system_id, "resource_id": p.resource_id, "link": p.link, "category": p.decision.category,
         "treatment": p.decision.treatment, "rule_id": p.decision.rule_id, "state": p.state.kind.value,
         "evidence_id": p.state.evidence_id, "decided_by": p.state.decided_by, "compliant": p.compliant}
        for p in plans
    ]
    return CaseResult(case.case_id, status, steps, trace)


def _no_resource(case: Case, ctx: AuditContext, subject: Subject, direct: dict, trace: dict) -> CaseResult:
    """Defined behaviour when no resource of the subject is observable at audit_at.

    The best-scoring profile deletion in the request window (even if another request also claims it) is the
    only footprint left: its account is verified ABSENT citing that deletion event. With no workflow at all
    there is nothing to audit, and the case is reported NOT_APPLICABLE with the reason in the trace (a COMPLETE
    row needs cited proof and an INCOMPLETE row needs a cited finding, neither of which exists)."""
    cands = ctx.linker.workflow_candidates(case, subject, direct, ctx.linker.lead_range, case.audit_at)
    if cands:
        acc = cands[0][1]
        trace["note"] = f"no observable resource; fell back to workflow {acc.activity_id}"
        steps = [Step("VERIFICATION", "account_service", acc.resource_id, "VERIFY_RECORD_ABSENT", "ABSENT", acc.activity_id)]
        return CaseResult(case.case_id, "COMPLETE", steps, trace)
    trace["note"] = "no observable resource and no deletion workflow for the subject"
    return CaseResult(case.case_id, "NOT_APPLICABLE", [], trace)
