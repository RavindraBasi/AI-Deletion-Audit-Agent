"""The deletion-audit agent: requests in, (status, steps) per case out."""
from collections import defaultdict
from dataclasses import dataclass

from . import dataset
from .identity import IdentityIndex, Subject
from .ingest import load_evidence
from .ingest.documents import image_extractor
from .intent import classify
from .linking import catalog_links, lifecycle_windows, tier1, tier2
from .models import Request
from .planner import plan
from .policy import Policy
from .state import resolve


@dataclass
class Case:
    request: Request
    subject: Subject | None
    authorized: bool


class DeletionAuditor:
    def __init__(self, llm, root=dataset.DATASET):
        self.llm = llm
        self.activities = dataset.load_activities(root)
        self.links, aliases = catalog_links(self.activities)
        self.observations, coverages = load_evidence(dataset.load_manifest(root), aliases,
                                                         image_extractor(llm), root)
        self.identity = IdentityIndex(dataset.load_identities(root), dataset.load_audit_log(root))
        self.policy = Policy(dataset.load_rules(root))
        self.obs_by_resource = defaultdict(list)
        for o in self.observations:
            self.obs_by_resource[(o.system, o.resource_id)].append(o)
        self.coverage_by_system = defaultdict(list)
        for c in coverages:
            self.coverage_by_system[c.system].append(c)
        self.worker_ops = defaultdict(set)
        self.activity_by_resource = defaultdict(list)
        for a in self.activities:
            self.activity_by_resource[a.resource_id].append(a)
            if a.source == "deletion_worker":
                self.worker_ops[a.resource_id].add(a.event_type)

    def run(self, requests):
        """Audit every request together (pipeline events are assigned across all of them)."""
        intents = classify([r.text for r in requests], self.llm)
        cases = [Case(r, self.identity.resolve(r.email), intents[r.text]) for r in requests]
        active = [c for c in cases if c.authorized and c.subject]
        windows = lifecycle_windows(active)
        linked = {c.request.case_id: tier1(c, windows[c.request.case_id], self.observations, self.links)
                  for c in active}
        files_of = defaultdict(set)
        for o in self.observations:
            files_of[o.resource_id].add(o.evidence_id)
        trail = tier2(active, linked, self.activities, files_of)
        results = {}
        for case in cases:
            cid = case.request.case_id
            if case not in active:
                results[cid] = ("NOT_APPLICABLE", [])
                continue
            resources = linked[cid] | trail.get(cid, set())
            results[cid] = plan([self._verdict(case.request, system, rid) for system, rid in sorted(resources)])
        return results

    def _verdict(self, request, system, rid):
        observations = self.obs_by_resource[(system, rid)]
        category = self.policy.category(system, observations, self.worker_ops[rid])
        treatment = self.policy.treatment(system, category)
        coverages = [c for c in self.coverage_by_system[system] if rid not in c.listed]
        violated, evidence = resolve(request, treatment, observations, coverages)
        if not evidence:   # no file speaks to the state: fall back to the latest visible activity
            seen = [a for a in self.activity_by_resource[rid] if a.time <= request.audit_at]
            evidence = max(seen, key=lambda a: a.time).activity_id if seen else ""
        return system, rid, treatment, violated, evidence
