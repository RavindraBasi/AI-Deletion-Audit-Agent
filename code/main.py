import csv
import json
import logging
import sys
from pathlib import Path
import pandas as pd

from code import config
from code.models import DeletionStatus, CaseResult
from code.core.identifier_resolver import IdentifierResolver
from code.core.ghost_detector import GhostDetector
from code.core.rule_engine import RuleEngine


def setup_logger(log_file: Path) -> logging.Logger:
    logger = logging.getLogger("ghost_data_audit")
    logger.setLevel(logging.INFO)

    # File handler
    fh = logging.FileHandler(log_file, mode="a", encoding="utf-8")
    fh.setLevel(logging.INFO)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh.setFormatter(formatter)
    logger.addHandler(fh)

    # Stream handler
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(formatter)
    logger.addHandler(sh)

    return logger


def main():
    logger = setup_logger(config.LOG_FILE)
    logger.info("Starting Ghost Data Still Exists Audit Agent execution")

    # Instantiate core logic modules
    try:
        resolver = IdentifierResolver()
        detector = GhostDetector()
        rule_engine = RuleEngine()
        logger.info("Successfully initialized core pipeline components")
    except Exception as e:
        logger.error(f"Failed to initialize pipeline components: {e}")
        sys.exit(1)

    # Load deletion requests
    try:
        requests_df = pd.read_csv(config.DELETION_REQUESTS_CSV)
        logger.info(f"Loaded {len(requests_df)} cases from {config.DELETION_REQUESTS_CSV}")
    except Exception as e:
        logger.error(f"Failed to read deletion requests file: {e}")
        sys.exit(1)

    results = []

    # Process each case
    for idx, row in requests_df.iterrows():
        case_id = str(row.get("case_id", "")).strip()
        email = str(row.get("email", "")).strip()
        request_text = str(row.get("request_text", ""))
        reported_status = str(row.get("reported_status", ""))
        audit_at = str(row.get("audit_at", ""))

        logger.info(f"Processing case {case_id} (email: {email})")

        try:
            # 1. Resolve identity footprint
            user_identity = resolver.resolve(email)

            # 2. Scan for ghost data and active processes
            findings = detector.scan(user_identity, audit_at=audit_at)

            # 3. Evaluate policy rules & generate CaseResult
            case_result = rule_engine.evaluate(
                case_id=case_id,
                request_text=request_text,
                findings=findings,
                reported_status=reported_status,
            )
            results.append(case_result)
            logger.info(
                f"Case {case_id} completed: status={case_result.deletion_status.value}, "
                f"steps_count={len(case_result.steps_json)}"
            )

        except Exception as e:
            logger.error(f"Unexpected error processing case {case_id}: {e}")
            # Fallback safe result
            fallback_result = CaseResult(
                case_id=case_id,
                deletion_status=DeletionStatus.INCOMPLETE,
                steps_json=[],
            )
            results.append(fallback_result)

    # Sort results by case_id ascending
    results.sort(key=lambda r: r.case_id)

    # Write results to output.csv
    try:
        with open(config.OUTPUT_FILE, mode="w", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(["case_id", "deletion_status", "steps_json"])

            for res in results:
                if res.deletion_status == DeletionStatus.NOT_APPLICABLE:
                    steps_json_str = "[]"
                else:
                    # Compact JSON serialization matching schema
                    steps_dicts = [step.model_dump() for step in res.steps_json]
                    steps_json_str = json.dumps(steps_dicts, separators=(",", ":"))

                writer.writerow([res.case_id, res.deletion_status.value, steps_json_str])

        logger.info(f"Successfully wrote {len(results)} rows to {config.OUTPUT_FILE}")

    except Exception as e:
        logger.error(f"Failed to write output file {config.OUTPUT_FILE}: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
