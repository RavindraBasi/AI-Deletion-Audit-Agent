import os
from pathlib import Path

# Base directories
BASE_DIR = Path(__file__).resolve().parent.parent
DATASET_DIR = Path(os.getenv("DATASET_DIR", BASE_DIR / "dataset"))
EVIDENCE_DIR = DATASET_DIR / "evidence"

# Dataset file paths
DELETION_REQUESTS_CSV = DATASET_DIR / "deletion_requests.csv"
DELETION_RULES_CSV = DATASET_DIR / "deletion_rules.csv"
USER_IDENTITIES_CSV = DATASET_DIR / "user_identities.csv"
USER_AUDIT_LOG_CSV = DATASET_DIR / "user_audit_log.csv"
SYSTEM_ACTIVITY_CSV = DATASET_DIR / "system_activity.csv"
EVIDENCE_FILES_CSV = DATASET_DIR / "evidence_files.csv"

# Output and Log file paths
OUTPUT_FILE = BASE_DIR / "output.csv"
LOG_FILE = BASE_DIR / "log.txt"
