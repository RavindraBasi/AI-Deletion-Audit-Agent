from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Any
from pathlib import Path
import json
import sqlite3
import pandas as pd

from code import config
from code.core.identifier_resolver import UserIdentity


@dataclass
class Finding:
    finding_type: str  # REPORT_REMAINING_RECORD, REPORT_EXPOSED_PII, REPORT_ACTIVE_DATA_FLOW, REPORT_RESTORE_RISK
    system_id: str
    resource_id: str
    state: str  # PRESENT or ACTIVE
    evidence_id: str
    details: str = ""
    matched_identifiers: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "finding_type": self.finding_type,
            "system_id": self.system_id,
            "resource_id": self.resource_id,
            "state": self.state,
            "evidence_id": self.evidence_id,
            "details": self.details,
            "matched_identifiers": self.matched_identifiers,
        }


class GhostDetector:
    def __init__(
        self,
        system_activity_path: Optional[Path] = None,
        evidence_files_path: Optional[Path] = None,
        evidence_dir: Optional[Path] = None,
    ):
        self.system_activity_path = system_activity_path or config.SYSTEM_ACTIVITY_CSV
        self.evidence_files_path = evidence_files_path or config.EVIDENCE_FILES_CSV
        self.evidence_dir = evidence_dir or config.EVIDENCE_DIR
        self._load_metadata()

    def _load_metadata(self) -> None:
        try:
            self.activity_df = pd.read_csv(self.system_activity_path)
        except Exception:
            self.activity_df = pd.DataFrame()

        try:
            self.evidence_files_df = pd.read_csv(self.evidence_files_path)
        except Exception:
            self.evidence_files_df = pd.DataFrame()

    def scan(self, user_identity: UserIdentity, audit_at: Optional[str] = None) -> List[Finding]:
        """
        Scan system activity logs and evidence files for the resolved user identifiers.
        """
        findings: List[Finding] = []
        identifiers = user_identity.all_identifiers()
        if not identifiers:
            return findings

        # Filter activity log by audit_at if present
        filtered_activity = self.activity_df
        if not filtered_activity.empty and audit_at and "event_time" in filtered_activity.columns:
            try:
                filtered_activity = filtered_activity[filtered_activity["event_time"] <= audit_at]
            except Exception:
                pass

        # 1. Scan System Activity
        if not filtered_activity.empty:
            for _, row in filtered_activity.iterrows():
                details = str(row.get("details", ""))
                resource_id = str(row.get("resource_id", ""))
                event_type = str(row.get("event_type", ""))
                activity_id = str(row.get("activity_id", ""))
                source_sys = str(row.get("source_system", ""))
                target_sys = str(row.get("target_system", ""))

                # Check identifier match in details or resource_id
                matched_ids = [id_str for id_str in identifiers if id_str in details or id_str in resource_id]
                if not matched_ids:
                    continue

                if event_type in ("CHECK_DATA_FLOW", "SYNC_RECORD"):
                    findings.append(
                        Finding(
                            finding_type="REPORT_ACTIVE_DATA_FLOW",
                            system_id=source_sys or "integration_service",
                            resource_id=resource_id,
                            state="ACTIVE",
                            evidence_id=activity_id,
                            details=details,
                            matched_identifiers=matched_ids,
                        )
                    )
                elif event_type in ("CHECK_RESTORE_ACCESS", "RESTORE_BACKUP"):
                    findings.append(
                        Finding(
                            finding_type="REPORT_RESTORE_RISK",
                            system_id=source_sys or "backup_service",
                            resource_id=resource_id,
                            state="ACTIVE",
                            evidence_id=activity_id,
                            details=details,
                            matched_identifiers=matched_ids,
                        )
                    )

        # 2. Scan Evidence Files
        if not self.evidence_files_df.empty:
            filtered_evidence_files = self.evidence_files_df
            if audit_at and "captured_at" in filtered_evidence_files.columns:
                try:
                    filtered_evidence_files = filtered_evidence_files[
                        filtered_evidence_files["captured_at"] <= audit_at
                    ]
                except Exception:
                    pass

            for _, row in filtered_evidence_files.iterrows():
                file_id = str(row.get("file_id", ""))
                file_path_str = str(row.get("file_path", ""))
                file_path = config.BASE_DIR / file_path_str if not file_path_str.startswith("/") else Path(file_path_str)

                if not file_path.exists():
                    continue

                try:
                    file_findings = self._scan_evidence_file(file_path, file_id, identifiers)
                    findings.extend(file_findings)
                except Exception:
                    pass

        # Deduplicate findings by (finding_type, system_id, resource_id, evidence_id)
        seen = set()
        unique_findings = []
        for f in findings:
            key = (f.finding_type, f.system_id, f.resource_id, f.evidence_id)
            if key not in seen:
                seen.add(key)
                unique_findings.append(f)

        return unique_findings

    def _scan_evidence_file(
        self, file_path: Path, file_id: str, identifiers: Set[str]
    ) -> List[Finding]:
        findings = []
        suffix = file_path.suffix.lower()

        # Handle JSON / TXT / LOG / SQL
        content_text = ""
        if suffix in (".json", ".txt", ".log", ".sql", ".csv"):
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content_text = f.read()
            except Exception:
                pass

        if content_text:
            matched_ids = [id_str for id_str in identifiers if id_str in content_text]
            if matched_ids:
                system_id = self._infer_system_id(file_path, content_text)
                resource_id = self._infer_resource_id(file_path, content_text, matched_ids)
                finding_type = self._infer_finding_type(content_text, matched_ids)

                findings.append(
                    Finding(
                        finding_type=finding_type,
                        system_id=system_id,
                        resource_id=resource_id,
                        state="PRESENT" if "REPORT_" in finding_type and "DATA_FLOW" not in finding_type and "RESTORE" not in finding_type else "ACTIVE",
                        evidence_id=file_id,
                        details=f"Match in {file_path.name}",
                        matched_identifiers=matched_ids,
                    )
                )

        return findings

    def _infer_system_id(self, file_path: Path, content: str) -> str:
        name = file_path.name.lower()
        if "account" in name:
            return "account_service"
        elif "search" in name:
            return "search_service"
        elif "support" in name:
            return "support_platform"
        elif "analytics" in name:
            return "analytics_platform"
        elif "backup" in name:
            return "backup_service"
        elif "export" in name:
            return "export_service"
        elif "integration" in name or "connector" in name:
            return "integration_service"
        elif "security" in name or "syslog" in name:
            return "security_service"
        return "system_service"

    def _infer_resource_id(self, file_path: Path, content: str, matched_ids: List[str]) -> str:
        # Default placeholder if resource id cannot be parsed from text
        return f"resource_{file_path.stem}"

    def _infer_finding_type(self, content: str, matched_ids: List[str]) -> str:
        if "data_flow" in content.lower() or "connector" in content.lower():
            return "REPORT_ACTIVE_DATA_FLOW"
        elif "backup" in content.lower() or "restore" in content.lower():
            return "REPORT_RESTORE_RISK"
        elif "exposed" in content.lower() or "pii" in content.lower() or len(matched_ids) > 1:
            return "REPORT_EXPOSED_PII"
        return "REPORT_REMAINING_RECORD"
