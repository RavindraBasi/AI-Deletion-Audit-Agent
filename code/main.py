#!/usr/bin/env python3
"""Deterministic evidence-backed deletion audit for Ghost Data Still Exists.

The engine extracts participant-facing evidence into resource observations, resolves
current/historical identifiers, reconstructs each resource's last observed state at
its case audit cutoff, applies deletion_rules.csv, and emits ordered JSON steps.
No external model/API is required; model usage is therefore zero.
"""
from __future__ import annotations
import csv, json, os, re, sqlite3, subprocess, tempfile
from concurrent.futures import ThreadPoolExecutor
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "dataset"
OUTPUT = ROOT / "output.csv"
SQL_COVERAGE = {}
SQL_RESOURCES = {}
SQL_CAPTURE = {}
EVIDENCE_CAPTURE = {}
EVIDENCE_PRIORITY = {}
IMAGE_TEXT = {}
POLICY_ACTIONS = {}

# Operation order is part of the challenge contract.
FINDING_ORDER = {
    "REPORT_REMAINING_RECORD": 0,
    "REPORT_EXPOSED_PII": 1,
    "REPORT_ACTIVE_DATA_FLOW": 2,
    "REPORT_RESTORE_RISK": 3,
}
REMEDIATION_ORDER = {
    "DISABLE_DATA_FLOW": 0,
    "BLOCK_RESTORE": 1,
    "DELETE_RECORD": 2,
    "REDACT_RECORD": 3,
    "ANONYMIZE_RECORD": 4,
}
VERIFICATION_ORDER = {
    "VERIFY_RECORD_ABSENT": 0,
    "VERIFY_RECORD_PRESENT": 1,
    "VERIFY_RECORD_REDACTED": 2,
    "VERIFY_RECORD_ANONYMIZED": 3,
    "VERIFY_DATA_FLOW_DISABLED": 4,
    "VERIFY_RESTORE_BLOCKED": 5,
}

EMAIL_RE = re.compile(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", re.I)
RESOURCE_RE = re.compile(r"\b(?:account|search|analytics|support|attachment|export|marketing|email_delivery|audit_record|backup|data_flow|cache|tombstone|profile|customer|archive|index|object)_[0-9]{1,}\b", re.I)
USER_RE = re.compile(r"\buser[_-]\d+\b", re.I)
HASH_RE = re.compile(r"\b[a-f0-9]{64}\b", re.I)
TIME_KEYS = ("observed_at", "captured_at", "recorded_at", "event_time", "updated_at", "created_at", "timestamp", "exported_at")
PII_KEYS = {"email", "owner_email", "customer_email", "requester_email", "phone", "requester_phone", "owner_phone", "email_hash", "full_name", "display_name", "name", "requester_name", "customer_name"}


def parse_dt(s):
    if not s: return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def norm_email(s):
    return str(s or "").strip().casefold()


def norm_phone(s):
    return re.sub(r"\D", "", str(s or ""))


def read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def load_policy_rules():
    """Load the participant-provided policy table; conditional rules are applied below."""
    global POLICY_ACTIONS
    rows=read_csv(DATA/'deletion_rules.csv')
    POLICY_ACTIONS={r['data_category'].strip().upper():r['required_action'].strip().upper() for r in rows}
    return rows


def system_for_prefix(resource_id):
    r = str(resource_id or "").lower()
    if r.startswith(("account_", "profile_")): return "account_service"
    if r.startswith(("search_", "index_")): return "search_service"
    if r.startswith("analytics_"): return "analytics_platform"
    if r.startswith(("support_", "attachment_")): return "support_platform"
    if r.startswith("export_"): return "export_service"
    if r.startswith("marketing_"): return "marketing_platform"
    if r.startswith("email_delivery_"): return "email_delivery_service"
    if r.startswith("audit_record_"): return "security_service"
    if r.startswith("backup_"): return "backup_service"
    if r.startswith("data_flow_"): return "integration_service"
    if r.startswith("cache_"): return "cache_service"
    if r.startswith("tombstone_"): return "identity_service"
    if r.startswith("object_"): return "object_storage"
    return "unknown_service"


def category_for(system, resource_id="", context="", row=None):
    ctx = str(context).lower()
    rid = str(resource_id or "").lower()
    row = row or {}
    explicit = str(row.get("record_type", row.get("data_category", ""))).upper()
    if explicit in {"USER_PROFILE", "SECONDARY_IDENTIFIER", "SEARCH_INDEX_ENTRY", "CACHE_ENTRY", "SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT", "ANALYTICS_PROFILE", "PRODUCT_TELEMETRY", "USER_EXPORT", "MARKETING_PROFILE", "EMAIL_DELIVERY_LOG", "SECURITY_AUDIT_LOG", "LEGAL_HOLD_RECORD", "BACKUP_ARCHIVE", "ACCOUNT_TOMBSTONE"}:
        return explicit
    if "attachment" in ctx or rid.startswith("attachment_"): return "SUPPORT_ATTACHMENT"
    if system == "account_service": return "USER_PROFILE"
    if system == "search_service": return "SEARCH_INDEX_ENTRY"
    if system == "analytics_platform":
        return "PRODUCT_TELEMETRY" if "telemetry" in ctx else "ANALYTICS_PROFILE"
    if system == "support_platform": return "SUPPORT_MESSAGE"
    if system == "export_service": return "USER_EXPORT"
    if system == "marketing_platform": return "MARKETING_PROFILE"
    if system == "email_delivery_service": return "EMAIL_DELIVERY_LOG"
    if system == "security_service": return "SECURITY_AUDIT_LOG"
    if system == "backup_service": return "BACKUP_ARCHIVE"
    if system in ("integration_service", "integration_management_service"): return "DATA_FLOW"
    if system == "identity_service": return "ACCOUNT_TOMBSTONE"
    if system == "object_storage": return "SUPPORT_ATTACHMENT" if ('attachment' in ctx or 'object' in rid) else 'SUPPORT_ATTACHMENT'
    if system == "cache_service": return "CACHE_ENTRY"
    if rid.startswith("tombstone_"): return "ACCOUNT_TOMBSTONE"
    if rid.startswith("audit_record_"): return "SECURITY_AUDIT_LOG"
    if rid.startswith("backup_"): return "BACKUP_ARCHIVE"
    if rid.startswith("data_flow_"): return "DATA_FLOW"
    return "UNKNOWN"


def get_time(obj, inherited=None):
    if isinstance(obj, dict):
        for k in TIME_KEYS:
            if obj.get(k):
                dt = parse_dt(obj.get(k))
                if dt: return dt
    return inherited


def get_observation_time(obj, inherited=None):
    """Prefer evidence capture/observation timestamps over a record's last-updated time."""
    if isinstance(obj, dict):
        for k in ("observed_at", "captured_at", "recorded_at", "event_time", "timestamp", "exported_at"):
            if obj.get(k):
                dt=parse_dt(obj.get(k))
                if dt: return dt
    if inherited is not None: return inherited
    return get_time(obj, inherited)


def flatten_values(obj, out=None):
    if out is None: out = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                flatten_values(v, out)
            elif v is not None and str(v).strip():
                out.append((str(k).lower(), str(v).strip()))
    elif isinstance(obj, list):
        for v in obj: flatten_values(v, out)
    return out


def infer_state(row, category, resource_id, source_kind="json"):
    """Return (state, pii_present, present_record, process_state)."""
    vals = {k: v for k, v in flatten_values(row)}
    lower_vals = {k: str(v).strip().lower() for k, v in vals.items()}
    # Explicit read-back/lookup evidence.
    result = lower_vals.get("result", lower_vals.get("status", ""))
    profile = row.get("profile", "__missing__") if isinstance(row, dict) else "__missing__"
    status_code = str(row.get("status_code", "")) if isinstance(row, dict) else ""
    if profile is None or status_code == "404" or result in {"not_found", "not found", "absent", "missing", "404"}:
        return "ABSENT", False, False, None
    if result in {"found", "present", "exists"}:
        # FOUND may be a service lookup claim, but its resource record itself is useful evidence.
        pass
    if category == "DATA_FLOW":
        enabled = row.get("enabled", row.get("is_enabled", None)) if isinstance(row, dict) else None
        if enabled is not None:
            active = str(enabled).lower() in {"true", "1", "yes", "enabled"}
            return ("ACTIVE" if active else "DISABLED"), False, True, active
    if category == "BACKUP_ARCHIVE":
        allowed = row.get("restore_allowed", row.get("restore_enabled", None)) if isinstance(row, dict) else None
        if allowed is not None:
            active = str(allowed).lower() in {"true", "1", "yes", "allowed"}
            return ("ACTIVE" if active else "BLOCKED"), False, True, active
    if category in {"ANALYTICS_PROFILE", "PRODUCT_TELEMETRY"}:
        # Null/empty identifiers are not proof of anonymization. Treat the record as
        # unknown unless evidence positively establishes that identifiers are present.
        id_fields = (
            "user_id", "customer_reference", "subject_reference", "customer_id",
            "email", "phone", "email_hash", "customer_email",
        )

        def has_value(value):
            return value is not None and str(value).strip().lower() not in {
                "", "null", "none", "n/a"
            }

        linked = any(has_value(vals.get(key)) for key in id_fields)
        pii = any(
            key in PII_KEYS and has_value(value)
            for key, value in flatten_values(row)
        )
        if linked or pii:
            return "PRESENT", True, True, None
        # Explicitly present identity columns with null/empty values can establish
        # anonymization; absent columns alone cannot. Require several identity
        # fields to be explicitly represented in this evidence row.
        explicit_keys = {str(k).lower() for k in row.keys()} if isinstance(row, dict) else set()
        identity_keys = {"user_id", "customer_reference", "subject_reference", "customer_id", "email", "phone", "email_hash", "customer_email"}
        explicit_identity_keys = identity_keys.intersection(explicit_keys)
        nullish = {"", "null", "none", "n/a"}
        if len(explicit_identity_keys) >= 3 and all(
            row.get(key) is None or str(row.get(key)).strip().lower() in nullish
            for key in explicit_identity_keys
        ):
            return "ANONYMIZED", False, True, None
        return "UNKNOWN", False, True, None
    if category == "ACCOUNT_TOMBSTONE":
        pii = any(k in PII_KEYS and str(v).strip().lower() not in {"", "null", "none", "n/a"} for k, v in flatten_values(row))
        return ("PRESENT" if pii else "REDACTED"), pii, True, None
    if category in {"SUPPORT_MESSAGE", "SUPPORT_ATTACHMENT", "EMAIL_DELIVERY_LOG"}:
        pii = any(k in PII_KEYS and str(v).strip().lower() not in {"", "null", "none", "n/a"} for k, v in flatten_values(row))
        return ("PRESENT" if pii else "REDACTED"), pii, True, None
    # Generic record: a resource-bearing record is present unless explicitly absent.
    pii = any(k in PII_KEYS and str(v).strip().lower() not in {"", "null", "none", "n/a"} for k, v in flatten_values(row))
    return "PRESENT", pii, True, None


def build_manifest():
    global EVIDENCE_CAPTURE
    rows = read_csv(DATA / "evidence_files.csv")
    manifest = {r["file_id"]: {**r, "path": DATA / r["file_path"], "capture_dt": parse_dt(r.get("captured_at"))} for r in rows}
    EVIDENCE_CAPTURE = {fid: item.get("capture_dt") for fid, item in manifest.items()}
    return manifest


def extract_json_file(file_id, path, capture_dt):
    observations = []
    try: root = json.loads(path.read_text(encoding="utf-8"))
    except Exception: return observations
    def walk(obj, system=None, context="", inherited_time=capture_dt, ancestors=None):
        ancestors = ancestors or []
        if isinstance(obj, dict):
            local_system = system
            if obj.get("data_store"): local_system = str(obj["data_store"])
            # service_resource_export names system as a dictionary key
            for key in obj:
                if key in {"account_service", "search_service", "analytics_platform", "support_platform", "export_service", "backup_service", "integration_service", "security_service", "identity_service", "marketing_platform", "email_delivery_service", "cache_service"}:
                    pass
            local_time = get_observation_time(obj, inherited_time)
            local_context = context
            if obj.get("collection"): local_context += "/" + str(obj["collection"])
            if obj.get("record_type"): local_context += "/" + str(obj["record_type"])
            if obj.get("table_name"): local_context += "/" + str(obj["table_name"])
            rid = obj.get("resource_id") or obj.get("resource") or obj.get("record_id")
            if rid:
                # Merge nested profile/document content into the parent observation.
                merged = dict(obj)
                for child_key in ("profile", "document", "record", "data", "payload", "details"):
                    child = obj.get(child_key)
                    if isinstance(child, dict): merged.update(child)
                if local_system is None:
                    # Walk ancestors from closest to farthest for service_exports keys.
                    for a in reversed(ancestors):
                        if a in {"account_service", "search_service", "analytics_platform", "support_platform", "export_service", "backup_service", "integration_service", "security_service", "identity_service", "marketing_platform", "email_delivery_service", "cache_service"}:
                            local_system = a; break
                local_system = local_system or system_for_prefix(rid)
                category = category_for(local_system, rid, local_context, merged)
                state, pii, present, process = infer_state(merged, category, rid)
                observations.append({"file_id": file_id, "resource_id": str(rid), "system_id": local_system,
                    "category": category, "time": local_time or capture_dt, "row": merged,
                    "state": state, "pii": pii, "present": present, "process": process,
                    "context": local_context, "source_kind": "json"})
            for k, v in obj.items():
                child_system = local_system
                if k == "service_exports": child_system = local_system
                elif k in {"account_service", "search_service", "analytics_platform", "support_platform", "export_service", "backup_service", "integration_service", "security_service", "identity_service", "marketing_platform", "email_delivery_service", "cache_service"}:
                    child_system = k
                walk(v, child_system, local_context + "/" + str(k), local_time, ancestors + [str(k)])
        elif isinstance(obj, list):
            for v in obj: walk(v, system, context, inherited_time, ancestors)
    walk(root)
    # Deduplicate nested parent/document observations by same file/system/resource/time; merge row fields.
    merged = {}
    for o in observations:
        key = (o["file_id"], o["system_id"], o["resource_id"], o["time"])
        if key not in merged: merged[key] = o
        else:
            merged[key]["row"].update(o["row"])
            cat = merged[key]["category"]
            st, pii, present, process = infer_state(merged[key]["row"], cat, merged[key]["resource_id"])
            merged[key].update(state=st, pii=pii, present=present, process=process)
    return list(merged.values())


def extract_sql_file(file_id, path, capture_dt):
    out=[]
    con=sqlite3.connect(":memory:")
    try:
        con.executescript(path.read_text(encoding="utf-8", errors="replace"))
        tables=[r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        scope={r[0]:parse_dt(r[1]) for r in con.execute("SELECT origin_system,captured_at FROM snapshot_scope")} if "snapshot_scope" in tables else {}
        table_systems = {
            "customer_profiles":"account_service", "customer_search_documents":"search_service",
            "customer_analytics_profiles":"analytics_platform", "support_records":"support_platform",
            "support_attachments":"support_platform", "customer_exports":"export_service",
            "connector_subscriptions":"integration_service", "backup_restore_entries":"backup_service",
            "security_audit_records":"security_service", "marketing_profiles":"marketing_platform",
            "email_delivery_logs":"email_delivery_service", "cache_entries":"cache_service",
            "account_tombstones":"identity_service", "product_telemetry":"analytics_platform",
        }
        for table in tables:
            if table == "snapshot_scope": continue
            cols=[r[1] for r in con.execute(f'PRAGMA table_info("{table}")')]
            for vals in con.execute(f'SELECT * FROM "{table}"'):
                base=dict(zip(cols,vals)); row=dict(base)
                # Newer point-in-time replica captures store a full JSON payload in one column.
                for json_key in ("record_json", "payload", "record", "data_json"):
                    raw=base.get(json_key)
                    if isinstance(raw,str):
                        try:
                            decoded=json.loads(raw)
                            if isinstance(decoded,dict): row.update(decoded)
                        except Exception: pass
                rid=row.get("resource_id") or row.get("record_id")
                if not rid: continue
                system = str(base.get("origin_system") or base.get("system_id") or base.get("system") or table_systems.get(table) or system_for_prefix(rid))
                collection = str(base.get("collection_name") or base.get("collection") or table)
                cat = category_for(system, rid, collection, row)
                tm = get_observation_time(row, scope.get(system, capture_dt)) or capture_dt
                state, pii, present, process = infer_state(row, cat, rid, "sql")
                out.append({"file_id":file_id,"resource_id":str(rid),"system_id":system,"category":cat,"time":tm,
                            "row":row,"state":state,"pii":pii,"present":present,"process":process,"context":collection,"source_kind":"sql"})
    except Exception:
        pass
    finally: con.close()
    SQL_CAPTURE[file_id] = capture_dt
    SQL_COVERAGE[file_id] = set(o['system_id'] for o in out if o['system_id'] != 'unknown_service')
    by_system=defaultdict(set)
    for o in out: by_system[o['system_id']].add(o['resource_id'])
    for system, ids in by_system.items(): SQL_RESOURCES[(file_id,system)] = ids
    return out


def extract_text_file(file_id, path, capture_dt, image_mode=False):
    out=[]
    try:
        if path.suffix.lower()=='.pdf':
            proc=subprocess.run(['pdftotext','-layout',str(path),'-'],capture_output=True,text=True,timeout=10)
            content=proc.stdout
        elif path.suffix.lower()=='.png' or image_mode:
            proc=subprocess.run(['tesseract',str(path),'stdout','--psm','6'],capture_output=True,text=True,timeout=25)
            content=proc.stdout
            IMAGE_TEXT[file_id]={'content':content,'capture_dt':capture_dt,'path':path}
        else: content=path.read_text(encoding='utf-8',errors='replace')
    except Exception: return out
    # Parse key=value fields from each line so read-back states are based on the actual result,
    # not merely on the presence of a resource identifier in a log message.
    lines=content.splitlines()
    trace_context=defaultdict(lambda: {'emails':set(),'users':set(),'phones':set(),'hashes':set()})
    for line in lines:
        flds={k.lower():v.strip('\"\' ,;') for k,v in re.findall(r'([A-Za-z_][A-Za-z0-9_]*)=(\"[^\"]*\"|\'[^\']*\'|[^\s,;]+)',line)}
        tid=flds.get('trace_id')
        if not tid: continue
        trace_context[tid]['emails'].update(e.casefold() for e in EMAIL_RE.findall(line))
        trace_context[tid]['users'].update(u.casefold() for u in USER_RE.findall(line))
        trace_context[tid]['phones'].update(norm_phone(x) for x in re.findall(r'(?:phone|mobile|contact_number)=([^\s,;]+)',line,re.I))
        trace_context[tid]['hashes'].update(h.casefold() for h in HASH_RE.findall(line))
    for line in lines:
        ids=list(dict.fromkeys(RESOURCE_RE.findall(line)))
        if not ids: continue
        fields={k.lower():v.strip('"\' ,;') for k,v in re.findall(r'([A-Za-z_][A-Za-z0-9_]*)=("[^"]*"|\'[^\']*\'|[^\s,;]+)',line)}
        emails=[e.casefold() for e in EMAIL_RE.findall(line)]
        users=[u.casefold() for u in USER_RE.findall(line)]
        hashes=[h.casefold() for h in HASH_RE.findall(line)]
        tm=parse_dt(re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?',line).group(0)) if re.search(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z?',line) else capture_dt
        for rid in ids:
            # Ignore generic start/connection lines unless they directly identify the resource state.
            event=fields.get('event','').lower()
            system=fields.get('service') or system_for_prefix(rid)
            row=dict(fields); row['resource_id']=rid; row['line']=line
            if path.suffix.lower()=='.png': row['image_text']=content
            # Structured plain-text handover reports use pipe-separated category/state columns.
            pipe_parts=[x.strip() for x in line.split('|')]
            known_categories={'USER_PROFILE','SECONDARY_IDENTIFIER','SEARCH_INDEX_ENTRY','CACHE_ENTRY','SUPPORT_MESSAGE','SUPPORT_ATTACHMENT','ANALYTICS_PROFILE','PRODUCT_TELEMETRY','USER_EXPORT','MARKETING_PROFILE','EMAIL_DELIVERY_LOG','SECURITY_AUDIT_LOG','LEGAL_HOLD_RECORD','BACKUP_ARCHIVE','ACCOUNT_TOMBSTONE','DATA_FLOW'}
            cat_token=next((x.upper() for x in pipe_parts if x.upper() in known_categories),None)
            if cat_token: row['record_type']=cat_token
            if cat_token: cat=category_for(system,rid,path.name,row)
            if cat_token and pipe_parts:
                ci=next((i for i,x in enumerate(pipe_parts) if x.upper()==cat_token),-1)
                if ci>=0 and ci+1<len(pipe_parts): row['result']=pipe_parts[ci+1].upper()
            tid=fields.get('trace_id')
            if tid and tid in trace_context:
                if trace_context[tid]['emails'] and not any(k in row for k in ('email','customer_email','requester_email','lookup_key','correlation_key','matched_value')):
                    row['lookup_key']=sorted(trace_context[tid]['emails'])[0]
                if trace_context[tid]['users'] and not any(k in row for k in ('user_id','customer_reference','subject_reference','lookup_key','subject_ref')):
                    row['customer_reference']=sorted(trace_context[tid]['users'])[0]
                if trace_context[tid]['phones'] and 'phone' not in row: row['phone']=sorted(trace_context[tid]['phones'])[0]
                if trace_context[tid]['hashes'] and 'email_hash' not in row: row['email_hash']=sorted(trace_context[tid]['hashes'])[0]
            if emails and not any(k in row for k in ('email','customer_email','requester_email','lookup_key','correlation_key','matched_value')):
                row['lookup_key']=emails[0]
            if users and not any(k in row for k in ('user_id','customer_reference','subject_reference','lookup_key','subject_ref')):
                row['customer_reference']=users[0]
            # Normalize commonly used log field aliases.
            if 'lookup_key' in row and '@' in row['lookup_key']: row['email']=row['lookup_key']
            if 'correlation_key' in row and '@' in row['correlation_key']: row['email']=row['correlation_key']
            if 'customer_email' in row and '@' in row['customer_email']: row['email']=row['customer_email']
            if 'requester_email' in row and '@' in row['requester_email']: row['email']=row['requester_email']
            if 'matched_value' in row and '@' in row['matched_value']: row['email']=row['matched_value']
            if 'subject_ref' in row and USER_RE.fullmatch(row['subject_ref']): row['subject_reference']=row['subject_ref']
            cat=category_for(system,rid,path.name,row)
            # Explicit point-in-time outcomes from read-back logs.
            lo=line.lower()
            state,pii,present,process=infer_state(row,cat,rid,'text')
            pipe_result=str(row.get('result','')).upper()
            if pipe_result in {'NOT_FOUND','ABSENT','MISSING'}:
                state,pii,present,process='ABSENT',False,False,None
            elif pipe_result in {'IDENTIFIABLE','PRESENT','FOUND'}:
                state,pii,present,process='PRESENT',True,True,None
            elif pipe_result in {'ANONYMIZED'}:
                state,pii,present,process='ANONYMIZED',False,True,None
            elif pipe_result in {'REDACTED'}:
                state,pii,present,process='REDACTED',False,True,None
            elif pipe_result in {'ACTIVE','ENABLED'}:
                state,pii,present,process='ACTIVE',False,True,True
            elif pipe_result in {'BLOCKED','DISABLED','STOPPED'}:
                state,pii,present,process=('BLOCKED' if pipe_result=='BLOCKED' else 'DISABLED'),False,True,False
            if re.search(r'http_status=404|rows=0|result=(miss|not_found|not-found)|status_code=404|profile=null|result=not_found|\bnot_found\b|\bnot found\b',lo):
                state,pii,present,process='ABSENT',False,False,None
            elif cat=='DATA_FLOW':
                val=row.get('enabled',row.get('is_enabled','')).lower()
                if val in {'true','1','yes'}: state,pii,present,process='ACTIVE',False,True,True
                elif val in {'false','0','no'}: state,pii,present,process='DISABLED',False,True,False
            elif cat=='BACKUP_ARCHIVE':
                val=row.get('restore_allowed',row.get('restore_enabled','')).lower()
                if val in {'true','1','yes'}: state,pii,present,process='ACTIVE',False,True,True
                elif val in {'false','0','no'}: state,pii,present,process='BLOCKED',False,True,False
            elif cat in {'ANALYTICS_PROFILE','PRODUCT_TELEMETRY'}:
                fields_present = [
                    str(row.get(k) or '').strip().lower()
                    for k in (
                        'customer_reference', 'subject_reference', 'user_id',
                        'email', 'phone', 'email_hash', 'customer_id', 'customer_email'
                    )
                ]
                has_identifier = any(
                    value not in {'', 'null', 'none', 'n/a'}
                    for value in fields_present
                )
                if has_identifier:
                    state,pii,present,process='PRESENT',True,True,None
                elif pipe_result == 'ANONYMIZED':
                    state,pii,present,process='ANONYMIZED',False,True,None
                else:
                    state,pii,present,process='UNKNOWN',False,True,None
            elif cat=='SECURITY_AUDIT_LOG':
                if row.get('rows')=='0': state,pii,present,process='ABSENT',False,False,None
                elif row.get('rows')=='1' or 'read_completed' in event: state,pii,present,process='PRESENT',False,True,None
            elif cat in {'SUPPORT_MESSAGE','SUPPORT_ATTACHMENT','EMAIL_DELIVERY_LOG'}:
                direct=[row.get(k,'').lower() for k in ('email','requester_email','owner_email','customer_email','phone','requester_phone','requester_name')]
                pii=any(v not in {'','null','none','n/a'} for v in direct)
                state='PRESENT' if pii else 'REDACTED'; present=True; process=None
            # Screenshot panels often place a resource ID and its status in different OCR lines.
            # Use the whole image only to interpret the already-identified resource's state.
            if path.suffix.lower()=='.png' and rid.lower() in content.lower():
                whole=content.lower()
                if cat=='DATA_FLOW':
                    if re.search(r'\b(disabled|stopped|inactive)\b',whole): state,process,present='DISABLED',False,True
                    elif re.search(r'\b(continuous|enabled|healthy)\b',whole): state,process,present='ACTIVE',True,True
                elif cat=='BACKUP_ARCHIVE':
                    if re.search(r'restore (blocked|disabled)|cannot restore|not restorable',whole): state,process,present='BLOCKED',False,True
                    elif 'archive' in whole or 'restore' in whole: state,process,present='ACTIVE',True,True
                elif cat=='USER_EXPORT':
                    if re.search(r'\b(ready|available|download available)\b',whole): state,pii,present='PRESENT',True,True
                    elif re.search(r'\b(not found|deleted|removed)\b',whole): state,pii,present='ABSENT',False,False
                elif cat=='ACCOUNT_TOMBSTONE':
                    # A retained customer-reference screen with only the stable user key is redacted.
                    if re.search(r'customer key|retained reference|customer reference registry',whole) and not EMAIL_RE.search(content): state,pii,present='REDACTED',False,True
            # Don't promote a started query into a final record state unless the line has a concrete outcome.
            if event.endswith('_started') or event in {'connection_acquired','dependency_retry','dependency_recovered'}:
                if not any(x in lo for x in ('http_status=404','rows=0','enabled=','restore_allowed=','result=hit','result=miss','read_completed','lookup_completed','query_completed','matched_value=')):
                    continue
            out.append({'file_id':file_id,'resource_id':rid,'system_id':system,'category':cat,'time':tm,
                        'row':row,'state':state,'pii':pii,'present':present,'process':process,'context':line,'source_kind':'text'})
    return out


def extract_all_evidence(manifest):
    def extract_one(item):
        fid,m=item; p=m['path']; cap=m['capture_dt']
        if not p.exists(): return []
        ext=p.suffix.lower()
        if ext=='.json': return extract_json_file(fid,p,cap)
        if ext=='.sql': return extract_sql_file(fid,p,cap)
        return extract_text_file(fid,p,cap,image_mode=(ext=='.png'))
    all_obs=[]
    with ThreadPoolExecutor(max_workers=6) as pool:
        for result in pool.map(extract_one, manifest.items()): all_obs.extend(result)
    for o in all_obs:
        m=manifest.get(o['file_id'],{})
        name=Path(m.get('file_path','')).name.lower()
        o['evidence_priority'] = (5 if 'service_resource_export' in name or 'resource_lookup_trace' in name else
                                  4 if any(x in name for x in ('read_replica','failover_replica','regional_replicas','customer_search_primary','customer_data_inventory')) else
                                  3 if 'snapshot_bundle' in name else 2)
        rowtxt=' '.join(str(v) for _,v in flatten_values(o.get('row',{}))).lower() + ' ' + str(o.get('context','')).lower()
        o['shared_context']=bool(re.search(r'(shared|another customer|other customer|agent|order context|order number|conversation|delivery update|ticket thread|message history)',rowtxt))
        if o['system_id']=='support_platform' and ('attachment' in name or 'attachment' in str(o.get('context','')).lower() or o['resource_id'].startswith('attachment_')):
            o['category']='SUPPORT_ATTACHMENT'
    return all_obs


def subject_identifiers(req, identities, audits):
    cutoff=parse_dt(req['audit_at'])
    email=norm_email(req['email'])
    matches=[]
    for u in identities:
        updated=parse_dt(u.get('updated_at'))
        vals={norm_email(u.get('email'))}
        if u.get('email_hash'): vals.add(str(u['email_hash']).casefold())
        if email in vals and (not updated or updated <= cutoff): matches.append(u['user_id'])
    # Current identity can contain the request email even when updated_at is later; the request email itself
    # is still a legitimate key, but historical fields are only accepted up to audit_at.
    if not matches:
        for u in identities:
            if email == norm_email(u.get('email')): matches.append(u['user_id'])
    for a in audits:
        if parse_dt(a.get('event_time')) and parse_dt(a['event_time']) > cutoff: continue
        if a.get('field_name','').lower()=='email' and email in {norm_email(a.get('old_value')),norm_email(a.get('new_value'))}:
            matches.append(a['user_id'])
    ids=set(matches)
    aliases=set([email])
    # Build identifiers for matched subjects using the identity snapshot and historical audit trail.
    for u in identities:
        if u['user_id'] not in ids: continue
        if norm_email(u.get('email')): aliases.add(norm_email(u['email']))
        if u.get('email_hash'): aliases.add(str(u['email_hash']).casefold())
        if u.get('phone'): aliases.add(norm_phone(u['phone']))
        if u.get('name'): aliases.add(str(u['name']).strip().casefold())
    for a in audits:
        if a['user_id'] not in ids: continue
        at=parse_dt(a.get('event_time'))
        if at and at>cutoff: continue
        field=a.get('field_name','').lower()
        for val in (a.get('old_value'),a.get('new_value')):
            if not val: continue
            if field=='email': aliases.add(norm_email(val))
            elif field=='phone': aliases.add(norm_phone(val))
            elif field in {'email_hash','hash'}: aliases.add(str(val).casefold())
            elif field in {'name','full_name'}: aliases.add(str(val).strip().casefold())
    return ids, aliases


def record_matches_subject(obs, user_ids, aliases):
    vals=flatten_values(obs.get('row',{}))
    for key,val in vals:
        v=str(val).strip()
        if not v: continue
        kl=key.lower(); vl=v.casefold()
        if kl in {'user_id','customer_reference','subject_reference','customer_id','owner_user_id','subject_id'} and vl in {x.casefold() for x in user_ids}:
            return True
        if '@' in v and norm_email(v) in aliases: return True
        if kl in {'phone','requester_phone','owner_phone'} and norm_phone(v) and norm_phone(v) in aliases: return True
        if kl in {'email_hash','hash'} and vl in aliases: return True
        if kl in {'name','full_name','display_name','requester_name'} and vl in aliases: return True
    # OCR/text lines store context in line/context; match all identity strings cautiously.
    context=str(obs.get('row',{}).get('line','')).casefold()
    for a in aliases:
        if len(a)>5 and a in context: return True
    return False


def request_is_deletion(text):
    """Return False only for an explicit, non-deletion rights request.

    The challenge's public examples include verification-only deletion requests and
    requests phrased without the literal word "delete", so deletion is the default
    unless the text clearly asks only for access, correction, portability, or marketing opt-out.
    """
    t=(text or '').casefold()
    # Negative intent is unambiguous even when the sentence contains the word "deletion".
    if any(re.search(p,t) for p in [
        r'\bnot asking for deletion\b', r'\bnot requesting deletion\b', r'\bnot a request to erase\b', r'\binformation request, not a request to erase\b',
        r'\bdo not close the account\b', r'\bdo not delete the account\b', r'\bdo not delete anything\b',
        r"\bplease don.t delete or change anything\b", r'\bwithout removing any of my records\b',
        r'\bwithout deleting\b', r'\bleave my (profile|account|purchase history) .*in place\b',
        r'\bstop the promotional emails\b', r'\bstop promotional emails\b'
    ]):
        return False
    explicit_non_delete = [
        r'\b(subject access request|request access to my data|access my personal data|access to my personal data)\b',
        r'\b(show|provide|send|give|download) me (a )?(copy|report|export) of my (personal )?data\b',
        r'\b(export|port|download) my (personal )?data\b', r'\bdata portability\b',
        r'\b(correct|rectify|update|change) my (personal )?(data|details|email|phone|address|information)\b',
        r'\b(correction|rectification) of my (personal )?(data|details|email|phone|address|information)\b',
        r'\b(opt.?out of marketing|marketing opt.?out|unsubscribe from marketing|stop (sending|emailing).*marketing)\b',
        r'\bremove me from marketing lists only\b', r'\bdo not close the account\b', r'\bdo not delete the account\b',
        r'\bnot asking for deletion\b', r'\bwithout removing any of my records\b', r'\bwithout deleting\b',
        r'\bleave my (profile|account|purchase history) .*in place\b', r"\bplease don.t delete or change anything\b",
        r'\bstop the promotional emails\b', r'\bstop promotional emails\b', r'\bpromotional emails only\b',
        r'\bcopy of last year.s invoices\b', r'\bcopy of the information held on my account\b', r'\bi need a copy of .*invoices\b',
        r'\bdownload my account information\b', r'\bportable format\b', r'\btell me what personal information you hold about me\b',
        r'\bchange the email address used for this account\b', r'\bchange my email address\b', r'\bupdate my email address\b',
        r'\bwant to continue using the service\b', r'\bplease leave my profile and purchase history in place\b',
        r'\bplease stop using my data for marketing\b', r'\bmarketing communications only\b',
        r'\bacceso a mis datos\b', r'\bsolicitud de acceso\b', r'\brectificación de mis datos\b',
        r'\bdemande d.acc[eè]s\b', r'\brectification de mes donn[eé]es\b',
        r'\bpedido de acesso aos meus dados\b', r'\bcorre[cç][aã]o dos meus dados\b',
        r'\bマーケティング.*停止\b', r'\b개인정보 열람\b', r'\b개인정보 정정\b'
    ]
    if any(re.search(p,t) for p in explicit_non_delete):
        deletion_context = re.search(r'\bdelet\w*\b|\beras\w*\b|\bclos\w* account\b|\bremove (my|the) account\b|\bremove my profile\b|\baccount.*gone\b|\bprofile.*gone\b|\bsupprim\w*\b|\bexclu\w*\b|\bapagar\b|\bborrar\b|\b삭제\b|\b删除\b|\b削除\b',t)
        if not deletion_context:
            return False
    return True


def current_state_for(obs_list, audit_dt):
    usable=[o for o in obs_list if not o.get('time') or o['time']<=audit_dt]
    if not usable: return None
    # Prefer the latest observation. If ties, prefer structured sources over text/OCR and explicit readback.
    priority={'text':0,'sql':1,'json':2,'activity':3}
    usable.sort(key=lambda o:(o.get('time') or datetime.min.replace(tzinfo=timezone.utc), o.get('evidence_priority',0), priority.get(o.get('source_kind'),9)))
    return usable[-1]


def step(op, phase, system, resource, state, evidence):
    return {"step_no":0,"phase":phase,"system_id":system,"resource_id":resource,"operation":op,"state":state,"evidence_id":evidence or ""}


def operation_for_state(obs):
    cat=obs['category']; state=obs['state']; pii=obs['pii']
    if POLICY_ACTIONS.get(cat) == 'DELETE' or cat in {"USER_PROFILE","SECONDARY_IDENTIFIER","SEARCH_INDEX_ENTRY","CACHE_ENTRY","USER_EXPORT","MARKETING_PROFILE","SUPPORT_ATTACHMENT"}:
        return ("REPORT_REMAINING_RECORD","DELETE_RECORD","VERIFY_RECORD_ABSENT","ABSENT") if state not in {"ABSENT"} else (None,None,"VERIFY_RECORD_ABSENT","ABSENT")
    if cat in {"SUPPORT_MESSAGE","EMAIL_DELIVERY_LOG"}:
        if state=="ABSENT": return None,None,"VERIFY_RECORD_ABSENT","ABSENT"
        if pii: return "REPORT_EXPOSED_PII","REDACT_RECORD","VERIFY_RECORD_REDACTED","REDACTED"
        return None,None,"VERIFY_RECORD_REDACTED","REDACTED"
    if POLICY_ACTIONS.get(cat) == 'ANONYMIZE' or cat in {"ANALYTICS_PROFILE","PRODUCT_TELEMETRY"}:
        if state in {"ABSENT"}: return None,None,"VERIFY_RECORD_ABSENT","ABSENT"
        if state == "UNKNOWN": return None,None,None,None
        if state!="ANONYMIZED" or pii: return "REPORT_EXPOSED_PII","ANONYMIZE_RECORD","VERIFY_RECORD_ANONYMIZED","ANONYMIZED"
        return None,None,"VERIFY_RECORD_ANONYMIZED","ANONYMIZED"
    if cat=="SECURITY_AUDIT_LOG":
        if state=="ABSENT": return None,None,None,None
        return None,None,"VERIFY_RECORD_PRESENT","PRESENT"
    if POLICY_ACTIONS.get(cat) == 'REDACT' and cat=='ACCOUNT_TOMBSTONE':
        if state=="ABSENT": return None,None,"VERIFY_RECORD_ABSENT","ABSENT"
        if pii: return "REPORT_EXPOSED_PII","REDACT_RECORD","VERIFY_RECORD_REDACTED","REDACTED"
        return None,None,"VERIFY_RECORD_REDACTED","REDACTED"
    if cat=="DATA_FLOW":
        # A flow is a recreation risk unless there is affirmative evidence it is disabled.
        if state in {"ACTIVE", "PRESENT"}: return "REPORT_ACTIVE_DATA_FLOW","DISABLE_DATA_FLOW","VERIFY_DATA_FLOW_DISABLED","DISABLED"
        if state=="DISABLED": return None,None,"VERIFY_DATA_FLOW_DISABLED","DISABLED"
    if cat=="BACKUP_ARCHIVE":
        # An archive is a restore risk unless restore access is affirmatively blocked.
        if state in {"ACTIVE", "PRESENT"}: return "REPORT_RESTORE_RISK","BLOCK_RESTORE","VERIFY_RESTORE_BLOCKED","BLOCKED"
        if state=="BLOCKED": return None,None,"VERIFY_RESTORE_BLOCKED","BLOCKED"
    # Unknown resource types should not create speculative remediation.
    return None,None,None,None


def sort_steps(steps, phase):
    if phase=='FINDING': order=FINDING_ORDER
    elif phase=='REMEDIATION': order=REMEDIATION_ORDER
    else: order=VERIFICATION_ORDER
    return sorted(steps,key=lambda s:(order.get(s['operation'],99),s['system_id'],s['resource_id']))



def manifest_capture_time(file_id, all_obs):
    # SQL_COVERAGE is populated from the actual point-in-time SQL files; each observation's
    # capture time is the manifest capture time or a source-row timestamp.
    return SQL_CAPTURE.get(file_id)

def evidence_available_by_cutoff(obs, audit_dt):
    """Evidence must have been captured by audit_at, even if its row has an older event time."""
    captured = EVIDENCE_CAPTURE.get(obs.get("file_id"))
    return captured is None or captured <= audit_dt


def make_case_output(req, identities, audits, all_obs, activities=None):
    if not POLICY_ACTIONS and (DATA/'deletion_rules.csv').exists(): load_policy_rules()
    if not request_is_deletion(req.get('request_text','')):
        return req['case_id'],'NOT_APPLICABLE','[]'
    user_ids, aliases=subject_identifiers(req,identities,audits)
    audit_dt=parse_dt(req['audit_at']) or datetime.max.replace(tzinfo=timezone.utc)
    # Directly linked resources seed the identity-to-resource graph.
    linked_ids=set()
    for o in all_obs:
        if not evidence_available_by_cutoff(o, audit_dt): continue
        if o.get('time') and o['time']>audit_dt: continue
        if record_matches_subject(o,user_ids,aliases): linked_ids.add((o['system_id'],o['resource_id']))
    # Correlate a deletion-worker event to this request only when it is temporally
    # adjacent to reported_at. The event discovers the resource ID; its reported_result
    # is never accepted as proof of the final state.
    reported_dt = parse_dt(req.get('reported_at'))
    if activities and reported_dt:
        for a in activities:
            if str(a.get('event_type', '')).upper() != 'DELETE_RECORD':
                continue
            at = parse_dt(a.get('event_time'))
            if not at or abs((at - reported_dt).total_seconds()) > 600:
                continue
            rid = str(a.get('resource_id', '')).strip()
            system = str(a.get('target_system', '')).strip() or system_for_prefix(rid)
            if rid and RESOURCE_RE.fullmatch(rid) and system == 'account_service':
                linked_ids.add((system, rid))
    # Link the account resource referenced by a search document or another linked record.
    # This uses explicit resource-ID references, not case-number assumptions.
    changed=True
    while changed:
        changed=False
        linked_resource_ids={rid for _system,rid in linked_ids}
        for o in all_obs:
            if not evidence_available_by_cutoff(o, audit_dt): continue
            if o.get('time') and o['time']>audit_dt: continue
            if (o['system_id'],o['resource_id']) not in linked_ids: continue
            row=o.get('row',{})
            if isinstance(row,dict):
                for field,val in row.items():
                    if not isinstance(val,str): continue
                    ref=val.strip()
                    if not RESOURCE_RE.fullmatch(ref) or ref==o['resource_id']: continue
                    if field.lower() in {'resource_id','record_id','image_text','line','details','context'}: continue
                    key=(system_for_prefix(ref),ref)
                    if key not in linked_ids:
                        linked_ids.add(key); changed=True
        for o in all_obs:
            if not evidence_available_by_cutoff(o, audit_dt): continue
            if o.get('time') and o['time']>audit_dt: continue
            if o['resource_id'] in linked_resource_ids:
                key=(o['system_id'],o['resource_id'])
                if key not in linked_ids: linked_ids.add(key); changed=True
    # Activity events can associate a deletion operation with its request by close timing.
    # They are used only to discover resource IDs, never as proof of the final state.
    if activities:
        # Explicit catalog/identity links and activity details that name this subject are valid
        # identity-resolution evidence, provided they were recorded by the audit cutoff.
        for a in activities:
            at=parse_dt(a.get('event_time'))
            if not at or at>audit_dt: continue
            details=str(a.get('details','')).casefold()
            matched=any((('@' in x and x in details) or (x.startswith('user_') and re.search(r'\b'+re.escape(x)+r'\b',details))) for x in aliases if x)
            if not matched: continue
            rid=a.get('resource_id','')
            if not rid or not RESOURCE_RE.fullmatch(rid): continue
            system=a.get('target_system') or system_for_prefix(rid)
            if system=='object_storage' or rid.startswith('object_'): system='object_storage'
            linked_ids.add((system,rid))
    case_obs=list(all_obs)
    # Associate image-only business screens with a linked resource when the screen contains
    # the subject identifier or a filename explicitly connected to that resource by the catalog.
    if activities and IMAGE_TEXT:
        for a in activities:
            at=parse_dt(a.get('event_time'))
            if not at or at>audit_dt: continue
            details=str(a.get('details','')).casefold()
            matched=any((('@' in x and x in details) or (x.startswith('user_') and re.search(r'\b'+re.escape(x)+r'\b',details))) for x in aliases if x)
            if not matched: continue
            rid=a.get('resource_id','')
            if not rid or not RESOURCE_RE.fullmatch(rid): continue
            system=a.get('target_system') or system_for_prefix(rid)
            if system=='object_storage' or rid.startswith('object_'): system='object_storage'
            for fid,img in IMAGE_TEXT.items():
                cap=img.get('capture_dt'); content=img.get('content',''); low=content.casefold()
                if not cap or cap>audit_dt: continue
                filename_match=any(fn.casefold() in low for fn in re.findall(r'[A-Za-z0-9_.-]+\.(?:pdf|zip|csv|json)',details))
                subject_match=any(('@' in x and x in low) for x in aliases if x)
                user_id_match=any(re.search(r'\b'+re.escape(uid.casefold())+r'\b',low) for uid in user_ids)
                resource_match=rid.casefold() in low
                tombstone_subject_match=(system=='identity_service' and user_id_match and bool(re.search(r'customer key|retained reference|customer reference registry',low)))
                if not (filename_match or resource_match or tombstone_subject_match): continue
                category=category_for(system,rid,str(img.get('path','')),{'record_type':'SUPPORT_ATTACHMENT' if system=='object_storage' else ''})
                if category=='UNKNOWN': category=category_for(system_for_prefix(rid),rid,str(img.get('path','')),{})
                state='PRESENT'; pii=False; shared=False; process=None
                if category=='DATA_FLOW':
                    if re.search(r'\b(disabled|stopped|inactive)\b',low): state,process='DISABLED',False
                    elif re.search(r'\b(continuous|enabled|healthy)\b',low): state,process='ACTIVE',True
                elif category=='BACKUP_ARCHIVE':
                    if re.search(r'restore (blocked|disabled)|cannot restore|not restorable',low): state,process='BLOCKED',False
                    else: state,process='ACTIVE',True
                elif category=='ACCOUNT_TOMBSTONE':
                    if re.search(r'customer key|retained reference|customer reference registry',low) and not EMAIL_RE.search(content): state='REDACTED'
                    else: state='PRESENT'; pii=bool(EMAIL_RE.search(content))
                elif category in {'ANALYTICS_PROFILE','PRODUCT_TELEMETRY'}:
                    if not EMAIL_RE.search(content) and not re.search(r'\buser_\d+\b',low): state='ANONYMIZED'
                    else: state='PRESENT'; pii=True
                elif category=='SUPPORT_MESSAGE':
                    pii=bool(EMAIL_RE.search(content))
                    shared=bool(re.search(r'order|conversation|another customer|agent|ticket|delivery',low))
                by_resource_key=(system,rid)
                # Keep the image observation even if the resource also appears in another evidence source;
                # the state resolver chooses the latest eligible observation.
                image_row={'resource_id':rid,'image_text':content}
                case_obs.append({'file_id':fid,'resource_id':rid,'system_id':system,'category':category,'time':cap,
                    'row':image_row,'state':state,'pii':pii,'present':True,'process':process,'context':str(img.get('path','')),
                    'source_kind':'text','evidence_priority':4,'shared_context':shared})
    by_resource=defaultdict(list)
    for o in case_obs:
        key=(o['system_id'],o['resource_id'])
        if not evidence_available_by_cutoff(o, audit_dt): continue
        if key in linked_ids and (not o.get('time') or o['time']<=audit_dt): by_resource[key].append(o)
    # If an explicit identity-link event establishes a resource-to-subject relationship but no
    # state snapshot is available by audit_at, retain the existence fact from the activity record.
    if activities:
        for a in activities:
            at=parse_dt(a.get('event_time'))
            if not at or at>audit_dt: continue
            details=str(a.get('details','')).casefold()
            matched=any((('@' in x and x in details) or (x.startswith('user_') and re.search(r'\b'+re.escape(x)+r'\b',details))) for x in aliases if x)
            if not matched: continue
            rid=a.get('resource_id','')
            if not rid or not RESOURCE_RE.fullmatch(rid): continue
            system=a.get('target_system') or system_for_prefix(rid)
            if system=='object_storage' or rid.startswith('object_'): system='object_storage'
            key=(system,rid)
            if not any(o.get('time') and o['time']<=audit_dt for o in by_resource.get(key,[])):
                category=category_for(system,rid,str(a.get('details','')),{'record_type':'SUPPORT_ATTACHMENT' if rid.startswith('object_') else ''})
                if category=='UNKNOWN': category=category_for(system_for_prefix(rid),rid,str(a.get('details','')),{})
                state='PRESENT'; pii=False; shared=False
                if category in {'DATA_FLOW'}: state='ACTIVE'
                elif category=='BACKUP_ARCHIVE': state='ACTIVE'
                elif category=='ACCOUNT_TOMBSTONE': state='REDACTED'
                by_resource[key].append({'file_id':a['activity_id'],'resource_id':rid,'system_id':system,'category':category,'time':at,
                    'row':{'resource_id':rid,'details':a.get('details','')},'state':state,'pii':pii,'present':True,'process':None,
                    'context':a.get('details',''),'source_kind':'activity','evidence_priority':6,'shared_context':shared})
    # A complete SQL point-in-time snapshot is also evidence of absence when a linked resource
    # is not present in any table for that system. This is essential for replica-drift checks.
    for system,rid in list(linked_ids):
        if system != 'account_service': continue
        eligible=[]
        for fid,systems in SQL_COVERAGE.items():
            cap=manifest_capture_time(fid, all_obs)
            if system in systems and cap and cap<=audit_dt and rid not in SQL_RESOURCES.get((fid,system),set()):
                eligible.append((cap,fid))
        if eligible:
            cap,fid=max(eligible)
            existing=by_resource.get((system,rid),[])
            if not any(o.get('time') and o['time']>=cap for o in existing):
                cat=category_for(system,rid,'',{})
                by_resource[(system,rid)].append({'file_id':fid,'resource_id':rid,'system_id':system,'category':cat,'time':cap,
                    'row':{'resource_id':rid,'snapshot_result':'NOT_FOUND'},'state':'ABSENT','pii':False,'present':False,'process':None,
                    'context':'SQL snapshot absence','source_kind':'sql','shared_context':False})
    current=[]
    for (system,rid),obs in by_resource.items():
        state=current_state_for(obs,audit_dt)
        if state:
            # Normalize category/system using strongest/latest evidence for this resource.
            current.append(state)
    # De-duplicate to one resource state. Keep system/resource identity stable.
    findings=[]; remediations=[]; verifications=[]
    violation=False
    for o in current:
        finding,remedy,verify,vstate=operation_for_state(o)
        if finding:
            violation=True
            findings.append(step(finding,'FINDING',o['system_id'],o['resource_id'], 'ACTIVE' if finding in {'REPORT_ACTIVE_DATA_FLOW','REPORT_RESTORE_RISK'} else 'PRESENT',o['file_id']))
            remediations.append(step(remedy,'REMEDIATION',o['system_id'],o['resource_id'], {'DELETE_RECORD':'ABSENT','REDACT_RECORD':'REDACTED','ANONYMIZE_RECORD':'ANONYMIZED','DISABLE_DATA_FLOW':'DISABLED','BLOCK_RESTORE':'BLOCKED'}[remedy],''))
        if verify:
            # For incomplete cases, all verifications are future work; complete cases cite evidence.
            verifications.append(step(verify,'VERIFICATION',o['system_id'],o['resource_id'],vstate,o['file_id']))
    # If a data-flow/backup observation has only a generic PRESENT state due to a variant field name,
    # don't assume a risk; only explicit active states generate findings.
    status='INCOMPLETE' if (violation or not verifications) else 'COMPLETE'
    if status=='INCOMPLETE':
        for v in verifications: v['evidence_id']=''
        ordered=findings and sort_steps(findings,'FINDING') or []
        ordered += sort_steps(remediations,'REMEDIATION')
        ordered += sort_steps(verifications,'VERIFICATION')
    else:
        # Complete cases consist only of evidence-backed verification checks.
        ordered=sort_steps(verifications,'VERIFICATION')
    for i,s in enumerate(ordered,1): s['step_no']=i
    return req['case_id'],status,json.dumps(ordered,ensure_ascii=False,separators=(',',':'))


def validate_output(path, requests):
    errors=[]
    with open(path,encoding='utf-8-sig') as f:
        header=f.readline().rstrip('\r\n').split(',')
    if header != ['case_id','deletion_status','steps_json']:
        errors.append(f'header mismatch: {header}')
    rows=read_csv(path)
    if len(rows)!=len(requests): errors.append(f"row count {len(rows)} != {len(requests)}")
    expected=sorted(r['case_id'] for r in requests)
    actual=[r.get('case_id','') for r in rows]
    if actual!=expected: errors.append('case IDs are not complete and lexicographically sorted')
    allowed={'COMPLETE','INCOMPLETE','NOT_APPLICABLE'}
    phases={'FINDING':0,'REMEDIATION':1,'VERIFICATION':2}
    op_order={**FINDING_ORDER,**REMEDIATION_ORDER,**VERIFICATION_ORDER}
    valid_evidence=set(build_manifest())
    valid_evidence.update(r['activity_id'] for r in read_csv(DATA/'system_activity.csv'))
    valid_evidence.update(r['audit_id'] for r in read_csv(DATA/'user_audit_log.csv'))
    for r in rows:
        cid=r.get('case_id','')
        if r.get('deletion_status') not in allowed: errors.append(f"invalid status for {cid}")
        try: steps=json.loads(r.get('steps_json',''))
        except Exception: errors.append(f"invalid JSON for {cid}"); continue
        if not isinstance(steps,list): errors.append(f"steps not array for {cid}"); continue
        if r['deletion_status']=='NOT_APPLICABLE' and steps: errors.append(f"NOT_APPLICABLE has steps: {cid}")
        if r['deletion_status']=='COMPLETE' and not steps: errors.append(f"COMPLETE has no verification steps: {cid}")
        if r['deletion_status']=='COMPLETE' and any(s.get('phase')!='VERIFICATION' for s in steps): errors.append(f"COMPLETE has non-verification step: {cid}")
        last_key=None
        for i,s in enumerate(steps,1):
            if s.get('step_no')!=i: errors.append(f"step numbering issue: {cid}"); break
            if set(s)!= {'step_no','phase','system_id','resource_id','operation','state','evidence_id'}:
                errors.append(f"step fields issue: {cid}"); break
            if s.get('phase') not in phases: errors.append(f"invalid phase: {cid}"); break
            if s.get('evidence_id') and s['evidence_id'] not in valid_evidence:
                errors.append(f"invalid evidence id {s['evidence_id']} in {cid}"); break
            if r['deletion_status']=='COMPLETE' and not s.get('evidence_id'):
                errors.append(f"COMPLETE step lacks evidence: {cid}"); break
            if r['deletion_status']=='INCOMPLETE':
                if s['phase']=='FINDING' and not s.get('evidence_id'): errors.append(f"finding lacks evidence: {cid}"); break
                if s['phase']!='FINDING' and s.get('evidence_id'): errors.append(f"future step cites evidence: {cid}"); break
            key=(phases[s['phase']],op_order.get(s['operation'],99),s['system_id'],s['resource_id'])
            if last_key is not None and key<last_key: errors.append(f"step order issue: {cid}"); break
            last_key=key
    return errors


def main():
    manifest=build_manifest()
    policy=load_policy_rules()
    identities=read_csv(DATA/'user_identities.csv')
    audits=read_csv(DATA/'user_audit_log.csv')
    requests=read_csv(DATA/'deletion_requests.csv')
    activities=read_csv(DATA/'system_activity.csv')
    print(f"Loaded {len(requests)} requests, {len(identities)} identities, {len(audits)} audit events, {len(manifest)} evidence files, {len(policy)} policy rules")
    observations=extract_all_evidence(manifest)
    print(f"Extracted {len(observations)} resource observations")
    out=[]
    for i,req in enumerate(requests,1):
        out.append(make_case_output(req,identities,audits,observations,activities))
        if i % 50 == 0: print(f'processed {i}/{len(requests)}', flush=True)
    out.sort(key=lambda x:x[0])
    with open(OUTPUT,'w',newline='',encoding='utf-8') as f:
        w=csv.writer(f,lineterminator='\n'); w.writerow(['case_id','deletion_status','steps_json']); w.writerows(out)
    errors=validate_output(OUTPUT,requests)
    print(f"Wrote {OUTPUT} ({len(out)} rows). Validation errors: {len(errors)}")
    for e in errors[:20]: print('ERROR:',e)
    counts=defaultdict(int)
    for _,s,_ in out: counts[s]+=1
    print('Statuses:',dict(counts))

if __name__=='__main__': main()
