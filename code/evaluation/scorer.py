"""Scores predicted output.csv rows against gold rows (SPEC §13.2 metrics)."""
from __future__ import annotations

import csv
import json
from collections import Counter
from pathlib import Path

FIELDS = ["phase", "system_id", "resource_id", "operation", "state", "evidence_id"]


def load_rows(path: Path) -> dict[str, tuple[str, list[dict]]]:
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["case_id"]: (r["deletion_status"], json.loads(r["steps_json"])) for r in csv.DictReader(fh)}


def _tuple(step: dict) -> tuple:
    return tuple(step.get(f, "") for f in FIELDS)


def score(pred: dict, gold: dict, case_ids: list[str] | None = None) -> dict:
    ids = sorted(case_ids if case_ids is not None else gold, key=lambda c: (len(c), c))
    status_ok = seq_ok = 0
    tp = fp = fn = 0
    field_hits = Counter()
    field_total = 0
    cite_hits = cite_total = 0
    op_confusion = Counter()
    per_case = []
    for cid in ids:
        g_status, g_steps = gold[cid]
        p_status, p_steps = pred.get(cid, ("<missing>", []))
        status_ok += p_status == g_status
        seq = [_tuple(s) for s in p_steps] == [_tuple(s) for s in g_steps]
        seq_ok += seq
        pc, gc = Counter(map(_tuple, p_steps)), Counter(map(_tuple, g_steps))
        inter = sum((pc & gc).values())
        tp += inter
        fp += sum(pc.values()) - inter
        fn += sum(gc.values()) - inter
        p_by_key = {(s["phase"], s["system_id"], s["resource_id"]): s for s in p_steps}
        for gs in g_steps:
            key = (gs["phase"], gs["system_id"], gs["resource_id"])
            ps = p_by_key.get(key)
            field_total += 1
            for f in FIELDS:
                field_hits[f] += bool(ps) and ps.get(f) == gs.get(f)
            op_confusion[(gs["operation"], ps["operation"] if ps else "<missing>")] += 1
            if gs.get("evidence_id"):
                cite_total += 1
                cite_hits += bool(ps) and ps.get("evidence_id") == gs["evidence_id"]
        for key, ps in p_by_key.items():
            if not any((g["phase"], g["system_id"], g["resource_id"]) == key for g in g_steps):
                op_confusion[("<none>", ps["operation"])] += 1
        per_case.append({"case_id": cid, "status_ok": p_status == g_status, "sequence_ok": seq,
                         "pred_status": p_status, "gold_status": g_status,
                         "missing": sorted(set(gc) - set(pc)), "extra": sorted(set(pc) - set(gc))})
    n = len(ids) or 1
    prec = tp / (tp + fp) if tp + fp else 0.0
    rec = tp / (tp + fn) if tp + fn else 0.0
    return {
        "cases": len(ids),
        "status_accuracy": status_ok / n,
        "sequence_accuracy": seq_ok / n,
        "step_precision": prec,
        "step_recall": rec,
        "step_f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
        "field_accuracy": {f: field_hits[f] / field_total if field_total else 0.0 for f in FIELDS},
        "citation_accuracy": cite_hits / cite_total if cite_total else 0.0,
        "op_confusion": {f"{g} -> {p}": c for (g, p), c in sorted(op_confusion.items())},
        "per_case": per_case,
    }
