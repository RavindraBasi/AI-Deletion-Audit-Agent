"""Paths, model choices, and contract constants."""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DATASET = REPO_ROOT / "dataset"
OUTPUT_CSV = REPO_ROOT / "output.csv"
CODE_DIR = REPO_ROOT / "code"
LLM_CACHE_DIR = CODE_DIR / ".llm_cache"
USAGE_REPORT = CODE_DIR / "evaluation" / "usage_report.md"
PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

INTENT_MODEL = "claude-haiku-5-5"
VISION_MODEL = "claude-sonnet-5-5"
# USD per million tokens (input, output) — Anthropic first-party list prices.
PRICES = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-sonnet-5-5": (2.00, 10.00),
}
INTENT_BATCH_SIZE = 200

# Operation order inside each phase, exactly as listed in the problem statement.
OPERATION_ORDER = [
    "REPORT_REMAINING_RECORD", "REPORT_EXPOSED_PII", "REPORT_ACTIVE_DATA_FLOW", "REPORT_RESTORE_RISK",
    "DISABLE_DATA_FLOW", "BLOCK_RESTORE", "DELETE_RECORD", "REDACT_RECORD", "ANONYMIZE_RECORD",
    "VERIFY_RECORD_ABSENT", "VERIFY_RECORD_PRESENT", "VERIFY_RECORD_REDACTED", "VERIFY_RECORD_ANONYMIZED",
    "VERIFY_DATA_FLOW_DISABLED", "VERIFY_RESTORE_BLOCKED",
]
PHASES = ["FINDING", "REMEDIATION", "VERIFICATION"]
