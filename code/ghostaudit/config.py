"""Paths (resolved from the repository root) and environment settings."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

CODE_DIR = Path(__file__).resolve().parent.parent
REPO_ROOT = CODE_DIR.parent
DEFAULT_DATASET_DIR = REPO_ROOT / "dataset"
DEFAULT_OUTPUT = REPO_ROOT / "output.csv"
CACHE_DIR = CODE_DIR / "cache"
PROMPTS_DIR = Path(__file__).resolve().parent / "llm" / "prompts"


@dataclass(frozen=True)
class DatasetPaths:
    root: Path
    requests: Path

    @property
    def identities(self) -> Path:
        return self.root / "user_identities.csv"

    @property
    def audit_log(self) -> Path:
        return self.root / "user_audit_log.csv"

    @property
    def activity(self) -> Path:
        return self.root / "system_activity.csv"

    @property
    def rules(self) -> Path:
        return self.root / "deletion_rules.csv"

    @property
    def manifest(self) -> Path:
        return self.root / "evidence_files.csv"

    @property
    def sample_requests(self) -> Path:
        return self.root / "sample_deletion_requests.csv"

    @property
    def sample_output(self) -> Path:
        return self.root / "sample_output.csv"

    @property
    def eval_requests(self) -> Path:
        return self.root / "deletion_requests.csv"


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def llm_backend() -> str:
    return env("GDA_LLM_BACKEND", "replay").strip().lower()


def budget_usd() -> float:
    try:
        return float(env("GDA_BUDGET_USD", "3.00"))
    except ValueError:
        return 3.0
