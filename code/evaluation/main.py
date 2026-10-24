#!/usr/bin/env python3
"""Evaluation harness: sample regression (dev/holdout), intent quality, e2e custom cases, invariants, and
the generated evaluation_report.md / usage_report.md.

    python3 code/evaluation/main.py                 # everything, writes both reports
    python3 code/evaluation/main.py --sample        # sample metrics only (fast)
    python3 code/evaluation/main.py --backend claude_cli   # also (re)classify the intent set live
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import re
import sys
import tempfile
import unittest
from collections import Counter, defaultdict
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
CODE = EVAL_DIR.parent
REPO = CODE.parent
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(EVAL_DIR))

from ghostaudit import loaders  # noqa: E402
from ghostaudit.config import DEFAULT_DATASET_DIR, DatasetPaths  # noqa: E402
from ghostaudit.intent import DELETE, NON_DELETE, IntentClassifier, heuristic  # noqa: E402
from ghostaudit.llm.adapter import LLMAdapter  # noqa: E402
from ghostaudit.llm.cache import LLMCache  # noqa: E402
from ghostaudit.llm.ledger import Ledger  # noqa: E402
from ghostaudit.pipeline import run  # noqa: E402
from scorer import load_rows, score  # noqa: E402

MODEL = CODE / "cache" / "workflow_model.json"
DEV = [f"case_{i:02d}" for i in range(1, 23)]
HOLDOUT = [f"case_{i:02d}" for i in range(23, 33)]


def pct(x: float) -> str:
    return f"{100 * x:.1f}%"


# ------------------------------------------------------------------ sample regression
def sample_metrics(tmp: Path, backend: str) -> dict:
    out = tmp / "sample_predictions.csv"
    paths = DatasetPaths(DEFAULT_DATASET_DIR, DEFAULT_DATASET_DIR / "sample_deletion_requests.csv")
    run(paths, out, backend=backend, ledger_dir=tmp / "ledger", workflow_model=MODEL)
    pred, gold = load_rows(out), load_rows(DEFAULT_DATASET_DIR / "sample_output.csv")
    return {"all": score(pred, gold), "dev": score(pred, gold, DEV), "holdout": score(pred, gold, HOLDOUT)}


# ------------------------------------------------------------------ intent quality
def load_intent_cases() -> list[dict]:
    rows = []
    for line in (EVAL_DIR / "intent_cases.md").read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\|\s*(i\d+)\s*\|\s*(\w+)\s*\|\s*(DELETE|NON_DELETE)\s*\|\s*([^|]*)\|\s*(.+?)\s*\|\s*$", line)
        if m:
            rows.append({"id": m[1], "lang": m[2], "expected": m[3], "trap": m[4].strip(), "text": m[5]})
    return rows


def classification_metrics(pairs: list[tuple[str, str]]) -> dict:
    conf = Counter(pairs)
    tp = conf[(NON_DELETE, NON_DELETE)]
    fp = conf[(DELETE, NON_DELETE)]
    fn = conf[(NON_DELETE, DELETE)]
    n = len(pairs) or 1
    return {
        "n": len(pairs),
        "accuracy": sum(conf[(a, a)] for a in (DELETE, NON_DELETE)) / n,
        "non_delete_precision": tp / (tp + fp) if tp + fp else 1.0,
        "non_delete_recall": tp / (tp + fn) if tp + fn else 1.0,
        "confusion": {f"{g}->{p}": c for (g, p), c in sorted(conf.items())},
    }


def intent_metrics(backend: str) -> dict:
    cases = load_intent_cases()
    samples = [{"id": c.case_id, "expected": DELETE, "text": c.request_text, "lang": "sample", "trap": "public sample"}
               for c in loaders.load_cases(DEFAULT_DATASET_DIR / "sample_deletion_requests.csv")]
    items = cases + samples
    llm = LLMAdapter(backend=backend, ledger=Ledger(directory=Path(tempfile.mkdtemp()) / "l"))
    clf = IntentClassifier(llm)
    labels = clf.classify_all([c["text"] for c in items])
    llm_pairs = [(c["expected"], labels[c["text"]].label) for c in items if labels[c["text"]].source == "llm"]
    heur_pairs = [(c["expected"], heuristic(c["text"]).label) for c in items]
    errors = []
    for c in items:
        lab, heu = labels[c["text"]], heuristic(c["text"])
        if lab.label != c["expected"] or heu.label != c["expected"]:
            errors.append({"id": c["id"], "lang": c["lang"], "trap": c["trap"], "expected": c["expected"],
                           "llm": lab.label if lab.source == "llm" else "n/a", "heuristic": heu.label, "text": c["text"]})
    # agreement on the evaluation texts (no labels): LLM vs heuristic
    eval_texts = [c.request_text for c in loaders.load_cases(DEFAULT_DATASET_DIR / "deletion_requests.csv")]
    clf2 = IntentClassifier(llm)
    lab2 = clf2.classify_all(eval_texts)
    uniq = sorted(set(eval_texts))
    with_llm = [t for t in uniq if lab2[t].source == "llm"]
    agree = sum(lab2[t].label == heuristic(t).label for t in with_llm)
    return {
        "llm": classification_metrics(llm_pairs), "heuristic": classification_metrics(heur_pairs), "errors": errors,
        "eval_unique_texts": len(uniq), "eval_llm_labelled": len(with_llm),
        "eval_agreement": agree / len(with_llm) if with_llm else None,
        "eval_non_delete": sorted(c.case_id for c in loaders.load_cases(DEFAULT_DATASET_DIR / "deletion_requests.csv")
                                  if lab2[c.request_text].label == NON_DELETE),
        "disagreements": [(t[:90], a.label, b.label) for t, a, b in clf2.disagreements()],
    }


# ------------------------------------------------------------------ custom e2e cases
def scenario_results() -> list[dict]:
    from tests.e2e.scenarios import SCENARIOS
    from tests.e2e.test_scenarios import run_scenario
    out = []
    for fn in SCENARIOS:
        with tempfile.TemporaryDirectory(prefix="gda_eval_") as tmp:
            expected, got, _ = run_scenario(fn, Path(tmp))
        ok = all(got.get(cid) == (st, steps) for cid, (st, steps) in expected.items())
        statuses = ", ".join(f"{cid}: {st}" for cid, (st, _) in expected.items())
        out.append({"name": fn.__name__, "ok": ok, "expected": statuses,
                    "doc": (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""})
    return out


# ------------------------------------------------------------------ usage
def usage_from_final_run(tmp: Path) -> dict:
    """Replays the final full-dataset run and attributes every LLM result it used to the call that produced it."""
    ledger_dir = tmp / "usage_ledger"
    paths = DatasetPaths(DEFAULT_DATASET_DIR, DEFAULT_DATASET_DIR / "deletion_requests.csv")
    res = run(paths, tmp / "final_output.csv", backend="replay", ledger_dir=ledger_dir, workflow_model=MODEL)
    cache = LLMCache()
    per_model: dict = defaultdict(lambda: {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "tasks": Counter(), "provider": ""})
    seen = set()
    for e in res.llm.ledger.entries:
        if e["key"] in seen:   # the same cached result can be requested twice (visual prefetch + extraction)
            continue
        seen.add(e["key"])
        rec = cache.get(e["key"]) or {}
        m = per_model[rec.get("model", e.get("model", "?"))]
        m["calls"] += 1
        m["tokens_in"] += int(rec.get("tokens_in", 0))
        m["tokens_out"] += int(rec.get("tokens_out", 0))
        m["cost_usd"] += float(rec.get("cost_usd", 0.0))
        m["tasks"][e["task"]] += 1
        m["provider"] = rec.get("provider", "")
    identical = (tmp / "final_output.csv").read_bytes() == (REPO / "output.csv").read_bytes() if (REPO / "output.csv").exists() else None
    from ghostaudit.llm.ledger import total_spend
    return {"per_model": per_model, "requests": len(res.results), "replay_hits": len(seen),
            "identical_to_output_csv": identical, "budget_limit": res.llm.budget.limit, "spent_total": total_spend()}


# ------------------------------------------------------------------ report rendering
def metrics_table(m: dict) -> list[str]:
    rows = ["| Split | Cases | Status acc. | Step P | Step R | Step F1 | Sequence acc. | Citation acc. |",
            "|---|---|---|---|---|---|---|---|"]
    for name in ("all", "dev", "holdout"):
        s = m[name]
        rows.append(f"| {name} | {s['cases']} | {pct(s['status_accuracy'])} | {pct(s['step_precision'])} | {pct(s['step_recall'])} "
                    f"| {pct(s['step_f1'])} | {pct(s['sequence_accuracy'])} | {pct(s['citation_accuracy'])} |")
    rows += ["", "Field-level accuracy (gold steps aligned on `(phase, system_id, resource_id)`):", "",
             "| Field | " + " | ".join(m["all"]["field_accuracy"]) + " |",
             "|---" * (len(m["all"]["field_accuracy"]) + 1) + "|",
             "| all | " + " | ".join(pct(v) for v in m["all"]["field_accuracy"].values()) + " |"]
    return rows


def render_report(sample: dict, intent: dict, scenarios: list[dict], inv: dict | None) -> str:
    notes = (EVAL_DIR / "report_notes.md").read_text(encoding="utf-8")
    g: list[str] = ["## 1. Sample regression (public labelled cases)", "",
                    "Pipeline run on `sample_deletion_requests.csv` and scored against `sample_output.csv`. "
                    "Dev = case_01–22 (used while building), holdout = case_23–32 (looked at only at milestones).", ""]
    g += metrics_table(sample)
    g += ["", "Per-operation alignment (gold operation -> predicted operation at the same resource/phase):", "",
          "| gold -> predicted | count |", "|---|---|"]
    g += [f"| {k} | {v} |" for k, v in sample["all"]["op_confusion"].items()]
    misses = [c for c in sample["all"]["per_case"] if not c["sequence_ok"]]
    g += ["", f"Cases whose step list is not an exact match ({len(misses)}):", ""]
    for c in misses:
        g.append(f"- `{c['case_id']}` status {c['pred_status']} (gold {c['gold_status']}); missing "
                 f"{', '.join(f'`{m[2]}`' for m in c['missing']) or '–'}; extra {', '.join(f'`{m[2]}`' for m in c['extra']) or '–'}")
    g += ["", "## 2. Intent classification", "",
          f"Labelled set: `code/evaluation/intent_cases.md` ({len(load_intent_cases())} own multilingual texts) + 32 public samples.", "",
          "| Classifier | n | Accuracy | NON_DELETE precision | NON_DELETE recall |", "|---|---|---|---|---|"]
    for name in ("llm", "heuristic"):
        s = intent[name]
        g.append(f"| {name} | {s['n']} | {pct(s['accuracy'])} | {pct(s['non_delete_precision'])} | {pct(s['non_delete_recall'])} |")
    g += ["", "Confusion (expected->predicted): " + "; ".join(f"**{n}** " + ", ".join(f"{k}={v}" for k, v in intent[n]["confusion"].items())
                                                         for n in ("llm", "heuristic")), ""]
    if intent["errors"]:
        g += ["Misclassified texts:", "", "| id | lang | trap | expected | llm | heuristic |", "|---|---|---|---|---|---|"]
        g += [f"| {e['id']} | {e['lang']} | {e['trap']} | {e['expected']} | {e['llm']} | {e['heuristic']} |" for e in intent["errors"]]
        g.append("")
    agree = intent["eval_agreement"]
    g += [f"Evaluation texts: {intent['eval_unique_texts']} unique, {intent['eval_llm_labelled']} labelled by the LLM; "
          f"LLM/heuristic agreement **{pct(agree) if agree is not None else 'n/a'}**, "
          f"{len(intent['disagreements'])} disagreement(s). NOT_APPLICABLE cases: {', '.join(intent['eval_non_delete'])}.", ""]
    passed = sum(s["ok"] for s in scenarios)
    g += ["## 3. Custom end-to-end evaluation cases", "",
          f"{passed}/{len(scenarios)} hand-labelled scenarios pass. Each writes a miniature dataset in the real file formats, runs "
          "`main.py` as a subprocess and compares every output step (code: `code/tests/e2e/`).", "",
          "| # | Scenario | Expected | Result |", "|---|---|---|---|"]
    g += [f"| {i} | `{s['name']}` | {s['expected']} | {'pass' if s['ok'] else '**FAIL**'} |" for i, s in enumerate(scenarios, 1)]
    if inv is not None:
        g += ["", "## 4. Invariants on the 518-case run (no labels needed)", "", "| Check | Result |", "|---|---|"]
        for k, v in inv.items():
            if isinstance(v, int):
                continue
            g.append(f"| {k} | {'pass' if not v else f'**{len(v)} failure(s)**: ' + '; '.join(v[:3])} |")
        g.append(f"| time-travel cases re-run with later evidence physically removed | {inv.get('time_travel_cases_checked', 0)} |")
    return "# Evaluation report\n\n" + "\n".join(g) + "\n\n" + notes


def render_usage(u: dict) -> str:
    lines = ["# LLM usage report", "",
             "Final full-dataset run: `python3 code/main.py` (backend `replay`) over the 518 requests in "
             "`dataset/deletion_requests.csv`, producing `output.csv`. That run is served entirely from the committed "
             "LLM cache, so its marginal cost is $0; the table attributes every cached result it used to the live call "
             "that produced it (Claude subscription through `claude -p`, cost = the CLI's API-equivalent `total_cost_usd`).", "",
             f"- Requests processed: **{u['requests']}**",
             f"- Cached LLM results used by the final run: **{u['replay_hits']}** (live calls during the final run: 0)",
             f"- Replay output byte-identical to the submitted `output.csv`: **{u['identical_to_output_csv']}**",
             f"- Spending cap (`GDA_BUDGET_USD`): ${u['budget_limit']:.2f}; live spend recorded in this checkout's development "
             f"ledgers (`code/cache/ledger/`, all trial runs included): ${u['spent_total']:.2f}", "",
             "| Provider | Model | Tasks | Calls | Input tokens | Output tokens | Total tokens | Cost (USD) |",
             "|---|---|---|---|---|---|---|---|"]
    tot = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
    for model, m in sorted(u["per_model"].items()):
        tasks = ", ".join(f"{t}×{n}" for t, n in sorted(m["tasks"].items()))
        lines.append(f"| {m['provider']} | `{model}` | {tasks} | {m['calls']} | {m['tokens_in']:,} | {m['tokens_out']:,} | "
                     f"{m['tokens_in'] + m['tokens_out']:,} | ${m['cost_usd']:.4f} |")
        for k in tot:
            tot[k] += m[k]
    lines.append(f"| **all** | | | **{tot['calls']}** | **{tot['tokens_in']:,}** | **{tot['tokens_out']:,}** | "
                 f"**{tot['tokens_in'] + tot['tokens_out']:,}** | **${tot['cost_usd']:.4f}** |")
    n = u["requests"] or 1
    lines += ["", "Per request (518 requests):", "",
              f"- Average tokens per request: **{(tot['tokens_in'] + tot['tokens_out']) / n:,.1f}** "
              f"({tot['tokens_in'] / n:,.1f} in / {tot['tokens_out'] / n:,.1f} out)",
              f"- Estimated cost per request: **${tot['cost_usd'] / n:.5f}**; estimated total: **${tot['cost_usd']:.4f}**", "",
              "Notes:", "",
              "- Intent: unique request texts are classified in batches of 25 by the `fast` alias (Claude Haiku 5.5).",
              "- Visual evidence: each of the 15 PNG/PDF files is read once by the `vision` alias (Claude Sonnet 5.5); "
              "screenshots that print no resource id get a second, file-listing pass.",
              "- Input token counts include prompt-cache reads/writes reported by the CLI (the CLI adds its own system prompt), "
              "which is why visual calls show large input counts at a small cost.",
              "- Everything else (parsing, linking, state reconstruction, policy, planning) is deterministic code: 0 tokens.",
              "- Re-running with an empty cache costs about the total above; `GDA_BUDGET_USD` caps it (default $3.00).", ""]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sample", action="store_true", help="sample metrics only")
    ap.add_argument("--backend", default="replay")
    ap.add_argument("--no-invariants", action="store_true")
    ap.add_argument("--full-invariants", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.ERROR)
    with tempfile.TemporaryDirectory(prefix="gda_eval_") as t:
        tmp = Path(t)
        sample = sample_metrics(tmp, args.backend)
        print("sample:", {k: {m: round(v[m], 4) for m in ("status_accuracy", "step_f1", "sequence_accuracy", "citation_accuracy")}
                          for k, v in sample.items()})
        if args.sample:
            return 0
        intent = intent_metrics(args.backend)
        print("intent:", {k: {m: round(intent[k][m], 4) for m in ("accuracy", "non_delete_precision", "non_delete_recall")} for k in ("llm", "heuristic")},
              "agreement:", intent["eval_agreement"])
        scenarios = scenario_results()
        print("scenarios:", f"{sum(s['ok'] for s in scenarios)}/{len(scenarios)}")
        inv = None
        if not args.no_invariants:
            import invariants
            inv = invariants.run_all(REPO / "output.csv", DEFAULT_DATASET_DIR, DEFAULT_DATASET_DIR / "deletion_requests.csv",
                                     full=args.full_invariants)
            print("invariants:", {k: (v if isinstance(v, int) else len(v)) for k, v in inv.items()})
        (EVAL_DIR / "evaluation_report.md").write_text(render_report(sample, intent, scenarios, inv), encoding="utf-8")
        usage = usage_from_final_run(tmp)
        (EVAL_DIR / "usage_report.md").write_text(render_usage(usage), encoding="utf-8")
        print("wrote", EVAL_DIR / "evaluation_report.md", "and", EVAL_DIR / "usage_report.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
