from dataclasses import dataclass, field
from typing import Dict, List, Optional, Set, Any
from pathlib import Path
import json
import re
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

        # 1. Scan System Activity CSV
        if not filtered_activity.empty:
            for _, row in filtered_activity.iterrows():
                details = str(row.get("details", ""))
                resource_id = str(row.get("resource_id", ""))
                event_type = str(row.get("event_type", ""))
                activity_id = str(row.get("activity_id", ""))
                source_sys = str(row.get("source_system", ""))
                target_sys = str(row.get("target_system", ""))

                # Check identifier match in details or resource_id
                matched_ids = [id_str for id_str in identifiers if id_str.lower() in details.lower() or id_str.lower() in resource_id.lower()]
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

                # Correct path resolution: check DATASET_DIR first
                p1 = config.DATASET_DIR / file_path_str
                p2 = config.BASE_DIR / file_path_str
                file_path = p1 if p1.exists() else (p2 if p2.exists() else None)

                if not file_path:
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

        # Handle JSON files
        if suffix == ".json":
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    data = json.load(f)
                findings.extend(self._scan_json_data(data, file_path, file_id, identifiers))
            except Exception:
                pass

        # Handle SQL / LOG / TXT / CSV files
        elif suffix in (".sql", ".log", ".txt", ".csv"):
            try:
                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content_text = f.read()
                findings.extend(self._scan_text_content(content_text, file_path, file_id, identifiers))
            except Exception:
                pass

        return findings

    def _scan_json_data(
        self, data: Any, file_path: Path, file_id: str, identifiers: Set[str]
    ) -> List[Finding]:
        findings = []

        def inspect(obj, current_system=None, current_resource=None):
            if isinstance(obj, dict):
                sys_id = obj.get("data_store") or obj.get("source") or obj.get("origin") or current_system
                res_id = obj.get("resource_id") or current_resource

                # Check if this node contains identifiers
                obj_str = json.dumps(obj)
                matched = [i for i in identifiers if i.lower() in obj_str.lower()]

                if matched and res_id:
                    finding_type = self._determine_finding_type(obj_str, matched, sys_id or "system_service")
                    state = "ACTIVE" if "DATA_FLOW" in finding_type or "RESTORE" in finding_type else "PRESENT"
                    findings.append(
                        Finding(
                            finding_type=finding_type,
                            system_id=sys_id or self._infer_system_from_filename(file_path),
                            resource_id=res_id,
                            state=state,
                            evidence_id=file_id,
                            details=f"Match in JSON {file_path.name}",
                            matched_identifiers=matched,
                        )
                    )
                else:
                    for k, v in obj.items():
                        inspect(v, sys_id, res_id)
            elif isinstance(obj, list):
                for item in obj:
                    inspect(item, current_system, current_resource)

        inspect(data)
        return findings

    def _scan_text_content(
        self, content: str, file_path: Path, file_id: str, identifiers: Set[str]
    ) -> List[Finding]:
        findings = []
        lines = content.splitlines()

        for line in lines:
            line_lower = line.lower()
            matched = [i for i in identifiers if i.lower() in line_lower]
            if not matched:
                continue

            # Extract system_id
            sys_id = None
            m_sys = re.search(r"service=([a-zA-Z0-9_]+)", line)
            if m_sys:
                sys_id = m_sys.group(1)

            # Extract resource_id
            res_id = None
            m_res = re.search(r"resource_id=([a-zA-Z0-9_]+)", line)
            if m_res:
                res_id = m_res.group(1)
            else:
                # Look for SQL VALUES ('resource_id', ...) or resource_id in quotes
                m_sql = re.search(r"VALUES\s*\(\s*'([a-zA-Z0-9_]+)'", line)
                if m_sql:
                    res_id = m_sql.group(1)

            if not sys_id:
                sys_id = self._infer_system_from_filename(file_path)
            if not res_id:
                res_id = f"resource_{file_path.stem}"

            finding_type = self._determine_finding_type(line, matched, sys_id)
            state = "ACTIVE" if "DATA_FLOW" in finding_type or "RESTORE" in finding_type else "PRESENT"

            findings.append(
                Finding(
                    finding_type=finding_type,
                    system_id=sys_id,
                    resource_id=res_id,
                    state=state,
                    evidence_id=file_id,
                    details=f"Match in {file_path.name}",
                    matched_identifiers=matched,
                )
            )

        return findings

    def _infer_system_from_filename(self, file_path: Path) -> str:
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
        return "account_service"

    def _determine_finding_type(self, text: str, matched_ids: List[str], system_id: str) -> str:
        text_lower = text.lower()
        if "data_flow" in system_id or "integration" in system_id or "connector" in text_lower or "data_flow" in text_lower:
            return "REPORT_ACTIVE_DATA_FLOW"
        elif "backup" in system_id or "restore" in text_lower or "backup" in text_lower:
            return "REPORT_RESTORE_RISK"
        elif "support" in system_id or "analytics" in system_id or "exposed" in text_lower or "redact" in text_lower or "pii" in text_lower:
            return "REPORT_EXPOSED_PII"
        return "REPORT_REMAINING_RECORD"
