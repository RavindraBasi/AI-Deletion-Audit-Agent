"""Case auditing: identity resolution, evidence linking, state reconstruction, decisions.

Pipeline for every request
  1. intent      - does the text authorise deletion?  (else NOT_APPLICABLE, [])
  2. identity    - current + historical emails, phones, hashes and the stable user_id
  3. linking     - resources whose evidence carries one of those identifiers, plus
                   data-catalog links, plus de-identified resources that the deletion
                   workflow touched for this request (activity batch attribution)
  4. state       - for each resource, the latest evidence captured at or before audit_at
  5. policy      - deletion_rules.csv category -> required final state -> violations
  6. steps       - ordered findings / remediation / verification
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta

from . import intent as intent_mod
from .evidence import Observation, system_for
from .policy import Rules, build_steps, categorize, is_violation

WORKFLOW_SOURCES = {"deletion_worker"}
POLICY_EVENTS = {"DELETE_RECORD", "ANONYMIZE_RECORD", "REDACT_RECORD", "DISABLE_DATA_FLOW", "BLOCK_RESTORE",
                 "CHECK_DATA_FLOW", "CHECK_RESTORE_ACCESS", "RETAIN_RECORD"}
WINDOW_BEFORE = timedelta(hours=2)
WINDOW_AFTER = timedelta(hours=2, minutes=30)
ACCOUNT_BEFORE = timedelta(hours=1, minutes=30)   # primary-profile deletes precede the status report
ACCOUNT_AFTER = timedelta(minutes=20)


def P(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def rtype(rid: str) -> str:
    return rid.rsplit("_", 1)[0]


class Auditor:
    def __init__(self, world, observations: list[Observation], metas: dict, llm=None):
        self.w = world
        self.metas = metas
        self.rules = Rules(world.rules)
        self.llm = llm
        self.obs_by_rid: dict[str, list[Observation]] = defaultdict(list)
        for o in observations:
            self.obs_by_rid[o.resource_id].append(o)
        # who does each resource belong to? (via identifiers seen in any evidence)
        self.owners: dict[str, set] = defaultdict(set)
        for rid, os_ in self.obs_by_rid.items():
            for o in os_:
                for k in o.keys:
                    self.owners[rid] |= world.key_owner.get(k, set())
        for rid, keys in world.catalog_links.items():
            for k in keys:
                self.owners[rid] |= world.key_owner.get(k, set())
        # an active connector / restore job that repopulated a person's resource is
        # itself a recreation path for that person's data
        import re as _re
        for a in world.activity:
            m = _re.search(r"repopulated downstream resource (\S+?)\.?$", a["details"])
            if m:
                self.owners[a["resource_id"]] |= self.owners.get(m.group(1), set())
                self.recreated_by = getattr(self, "recreated_by", {})
                self.recreated_by.setdefault(m.group(1), []).append(a)
        self.user_resources: dict[str, set] = defaultdict(set)
        for rid, us in self.owners.items():
            for u in us:
                self.user_resources[u].add(rid)
        self.file_systems: dict[str, set] = defaultdict(set)
        for o in observations:
            self.file_systems[o.file_id].add(o.system)
        for fid, meta in metas.items():
            self.file_systems[fid] |= set((meta.get("scope") or {}).keys())
        self.deletion_activity = [a for a in world.activity
                                  if a["source_system"] in WORKFLOW_SOURCES and a["event_type"] in POLICY_EVENTS]

    # ------------------------------------------------------------------ linking
    def _resource_time(self, rid: str) -> str:
        """Earliest moment the resource is evidenced (evidence, workflow activity, catalog link)."""
        ts = [o.ts for o in self.obs_by_rid.get(rid, [])]
        ts += [a["event_time"] for a in self.w.activity_by_resource.get(rid, [])
               if a["source_system"] in WORKFLOW_SOURCES or a["event_type"] == "LINK_RESOURCE_IDENTITY"]
        return min(ts) if ts else "9999"

    def link_all(self, requests: list[dict]):
        """Attach resources to every deletion request. Batch attribution needs all
        requests at once because one workflow run processes several requests."""
        self.case_users = {}
        self.case_links: dict[str, dict[str, str]] = {}   # case -> rid -> how linked
        self.intents = {}
        by_user = defaultdict(list)
        for r in requests:
            label, method = intent_mod.classify(r["request_text"], self.llm)
            self.intents[r["case_id"]] = (label, method)
            idents = self.w.resolve(r["email"])
            self.case_users[r["case_id"]] = idents
            self.case_links[r["case_id"]] = {}
            for ident in idents:
                by_user[ident.user_id].append(r)
        # identifier links; a person with several requests gets each resource attached
        # to the first request whose audit cut-off is not before the resource appeared
        for uid, reqs in by_user.items():
            reqs = sorted(reqs, key=lambda r: r["audit_at"])
            for rid in self.user_resources.get(uid, ()):
                t = self._resource_time(rid)
                chosen = next((r for r in reqs if t <= r["audit_at"]), None)
                if chosen is None:
                    continue    # first evidenced after every audit cut-off: a later episode
                self.case_links[chosen["case_id"]][rid] = "identifier"
        # ---- activity batch attribution for de-identified resources
        dels = [r for r in requests if self.intents[r["case_id"]][0] == "DELETE"]
        windows = {r["case_id"]: (P(r["reported_at"]) - WINDOW_BEFORE, P(r["reported_at"]) + WINDOW_AFTER)
                   for r in dels}
        req_by_case = {r["case_id"]: r for r in dels}
        case_num = {r["case_id"]: int(r["case_id"].split("_")[1]) for r in dels}
        # provisional cleanliness of each case from identifier-linked evidence only
        clean = {}
        for r in dels:
            clean[r["case_id"]] = not any(it["violation"] for it in self._items(r, self.case_links[r["case_id"]]))
        cand = defaultdict(list)       # rid -> [case]
        first_act = {}
        for a in self.deletion_activity:
            rid = a["resource_id"]
            if self.owners.get(rid):
                continue                     # belongs to a known person
            t = P(a["event_time"])
            for cid, (lo, hi) in windows.items():
                if lo <= t <= hi:
                    cand[rid].append(cid)
            first_act.setdefault(rid, a)
        case_files = {}
        for r in dels:
            files = set()
            for rid in self.case_links[r["case_id"]]:
                files |= {o.file_id for o in self.obs_by_rid.get(rid, [])}
            case_files[r["case_id"]] = files
        has_type = {cid: {rtype(rid) for rid in self.case_links[cid]} for cid in windows}
        assigned = set()
        status_of = {"SUCCESS": "COMPLETE", "PARTIAL": "PARTIALLY_COMPLETE", "FAILED": "NOT_STARTED"}
        # accounts first (every request has exactly one primary profile), then the rest
        order = sorted(cand, key=lambda x: (rtype(x) != "account", first_act[x]["event_time"], x))
        # pass 0: accounts whose workflow outcome matches the reported status;
        # pass 1: remaining accounts for requests still without one; then other resources
        passes = [("account", True), ("account", False), ("other", None)]
        for kind, strict in passes:
            for rid in order:
                t = rtype(rid)
                if (t == "account") != (kind == "account") or rid in assigned:
                    continue
                a = first_act[rid]
                seen_in = {o.file_id for o in self.obs_by_rid.get(rid, [])}
                if t != "account" and not seen_in:
                    continue                     # no evidence of the record itself: ignore
                cands = sorted(set(cand[rid]), key=lambda c: case_num[c])
                cands = [c for c in cands if t not in has_type[c]]
                if t == "account":
                    at = P(a["event_time"])
                    cands = [c for c in cands
                             if P(req_by_case[c]["reported_at"]) - ACCOUNT_BEFORE <= at
                             <= P(req_by_case[c]["reported_at"]) + ACCOUNT_AFTER]
                    if strict:
                        # the workflow's per-request outcome mirrors the status it reported
                        cands = [c for c in cands
                                 if status_of.get(a["reported_result"]) == req_by_case[c]["reported_status"]]
                else:
                    co = [c for c in cands if case_files[c] & seen_in]
                    cands = co or cands
                    cl = [c for c in cands if clean[c]]
                    cands = cl or cands
                if not cands:
                    continue
                chosen = cands[0]
                assigned.add(rid)
                self.case_links[chosen][rid] = "activity:" + a["activity_id"]
                has_type[chosen].add(t)
                case_files[chosen] |= seen_in

    # -------------------------------------------------------------------- state
    def state_at(self, rid: str, audit_at: str):
        cutoff = audit_at
        valid = [o for o in self.obs_by_rid.get(rid, []) if o.captured_at <= cutoff and o.ts <= cutoff]
        if not valid:
            return None
        valid.sort(key=lambda o: (o.ts, o.captured_at))
        last = valid[-1]
        same = [o for o in valid if o.file_id == last.file_id]
        # within one snapshot use its most recent statements
        latest_ts = max(o.ts for o in same)
        same = [o for o in same if o.ts == latest_ts] or same
        def comb(attr):
            vals = [getattr(o, attr) for o in same]
            if True in vals:
                return True
            if False in vals:
                return False
            return None
        return {"exists": comb("exists"), "identifiable": comb("identifiable"), "active": comb("active"),
                "file_id": last.file_id, "ts": latest_ts, "hints": set().union(*[o.hints for o in same])}

    # ------------------------------------------------------------------- decide
    def _items(self, r: dict, links: dict) -> list:
        audit_at = r["audit_at"]
        items = []
        for rid, how in sorted(links.items()):
            st = self.state_at(rid, audit_at)
            hints = set()
            for o in self.obs_by_rid.get(rid, []):
                hints |= o.hints
            acts = {a["event_type"] for a in self.w.activity_by_resource.get(rid, [])
                    if a["source_system"] in WORKFLOW_SOURCES}
            cat = categorize(rid, hints, acts)
            target = self.rules.target(cat)
            if st is None:
                violation, ev = False, ""
            else:
                violation = is_violation(target, st["exists"], st["identifiable"], st["active"])
                ev = st["file_id"]
            items.append({"system_id": system_for(rid), "resource_id": rid, "category": cat, "target": target,
                          "violation": violation, "evidence_id": ev, "linked_by": how,
                          "state": None if st is None else {k: st[k] for k in ("exists", "identifiable", "active", "ts")}})
        return items

    def _absence_evidence(self, r, items, main):
        """Evidence proving an account is gone when no file mentions it: a capture of the
        account store (at or before audit_at) in which the profile does not appear."""
        if main:
            # every statement of a case is proven by the capture that holds its records
            return main
        best = ""
        for fid, meta in self.metas.items():
            cap = meta.get("captured_at", "")
            if cap <= r["audit_at"] and "account" in meta.get("origin", "") and cap > self.metas.get(best, {}).get("captured_at", ""):
                best = fid
        return best or main

    def audit_case(self, r: dict) -> dict:
        cid = r["case_id"]
        label, method = self.intents[cid]
        result = {"case_id": cid, "intent": label, "intent_method": method,
                  "users": [i.user_id for i in self.case_users[cid]]}
        if label != "DELETE":
            result.update(status="NOT_APPLICABLE", steps=[], items=[])
            return result
        items = self._items(r, self.case_links[cid])
        files = [it["evidence_id"] for it in items if it["evidence_id"]]
        main = max(set(files), key=lambda f: (files.count(f), f)) if files else ""
        for it in items:
            if not it["evidence_id"]:
                it["evidence_id"] = self._absence_evidence(r, items, main) if it["target"] == "ABSENT" else main
        complete = not any(it["violation"] for it in items)
        result.update(status="COMPLETE" if complete else "INCOMPLETE", items=items,
                      steps=build_steps(items, complete))
        return result
