"""CPU evidence ingestion and temporal, identity-linked retrieval. No model calls."""
import csv
import hashlib
import json
import re
import sqlite3
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path


def read_csv(path):
    with Path(path).open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def tokens(value):
    # Exact normalized identifiers, rather than substring matches (user_1 != user_10).
    return set(re.findall(r'[\w.+@-]+', json.dumps(value, ensure_ascii=False).casefold()))


def resources(value):
    # Resource IDs in the supplied schema end in a numeric suffix. Service names
    # must never become graph bridges joining unrelated customers.
    found = set(re.findall(r'\b[a-z][a-z_]*_\d+\b', json.dumps(value)))
    return {v for v in found if not v.startswith(('user_', 'case_', 'activity_', 'audit_', 'evidence_', 'rule_'))}


class EvidenceIndex:
    def __init__(self, dataset, cache):
        self.dataset = Path(dataset).resolve()
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.identities = read_csv(self.dataset / 'user_identities.csv')
        self.history = read_csv(self.dataset / 'user_audit_log.csv')
        self.rules = read_csv(self.dataset / 'deletion_rules.csv')
        self.units = []
        self.coverage = []
        for item in read_csv(self.dataset / 'evidence_files.csv'):
            self.ingest(item)
        for row in read_csv(self.dataset / 'system_activity.csv'):
            self.add(row['activity_id'], row['event_time'], row['event_time'], row, 'activity_claim')
        self.inverted = defaultdict(set)
        self.by_resource = defaultdict(set)
        for i, unit in enumerate(self.units):
            for token in tokens(unit['content']):
                self.inverted[token].add(i)
            for rid in resources(unit['content']):
                self.by_resource[rid].add(i)

    def add(self, evidence_id, captured, observed, content, kind):
        self.units.append(dict(evidence_id=evidence_id, captured_at=captured,
                               observed_at=observed or captured, kind=kind, content=content))

    def ingest(self, item):
        path = (self.dataset / item['file_path']).resolve()
        if not path.is_relative_to(self.dataset):
            raise ValueError('Manifest path escapes dataset')
        captured = item['captured_at']
        eid = item['file_id']
        ext = path.suffix.lower()
        status = 'extracted'
        if ext == '.json':
            def walk(obj, context):
                if isinstance(obj, dict):
                    ctx = dict(context)
                    for k in ('captured_at', 'recorded_at', 'data_store', 'system', 'collection'):
                        if k in obj:
                            ctx[k] = obj[k]
                    if 'resource_id' in obj:
                        self.add(eid, captured, ctx.get('recorded_at', ctx.get('captured_at', captured)),
                                 {'context': ctx, 'record': obj}, 'json_record')
                    else:
                        for value in obj.values():
                            walk(value, ctx)
                elif isinstance(obj, list):
                    for value in obj:
                        walk(value, context)
            walk(json.loads(path.read_text()), {})
        elif ext == '.sql':
            db = sqlite3.connect(':memory:')
            try:
                # Restrict imported SQL to this transient database; no attached files or extensions.
                denied = {sqlite3.SQLITE_ATTACH, sqlite3.SQLITE_DETACH}
                def authorize(action, arg1, arg2, database, source):
                    if (action == sqlite3.SQLITE_PRAGMA and arg1 != 'foreign_keys') or action in denied or (action == sqlite3.SQLITE_FUNCTION and arg2 == 'load_extension'):
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                db.set_authorizer(authorize)
                db.executescript(path.read_text())
                db.row_factory = sqlite3.Row
                tables = db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
                for table in tables:
                    name = table['name']
                    quoted = '"' + name.replace('"', '""') + '"'
                    for row in db.execute('SELECT * FROM ' + quoted):
                        data = dict(row)
                        if 'record_json' in data:
                            data['record_json'] = json.loads(data['record_json'])
                        self.add(eid, captured, data.get('updated_at', captured),
                                 {'table': name, 'row': data}, 'sql_snapshot')
            finally:
                db.close()
        elif ext in ('.log', '.txt'):
            lines = path.read_text().splitlines()
            for n, line in enumerate(lines):
                # Include preceding lookup line to associate a readback with its subject.
                match = re.match(r'\d{4}-\d\d-\d\dT[\d:]+Z', line)
                content = '\n'.join(lines[max(0, n-1):n+1]) if 'completed' in line else line
                self.add(eid, captured, match[0] if match else captured, content, 'text')
        elif ext in ('.pdf', '.png', '.jpg', '.jpeg'):
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            target = self.cache / (digest + '.txt')
            if not target.exists():
                try:
                    if ext == '.pdf':
                        from pypdf import PdfReader
                        text = '\n'.join(page.extract_text() or '' for page in PdfReader(path).pages)
                        if not text.strip():
                            raise ValueError('PDF has no embedded text; OCR required')
                    else:
                        result = subprocess.run(['tesseract', str(path), 'stdout'], capture_output=True, text=True, check=True)
                        text = result.stdout
                        if not text.strip():
                            raise ValueError('OCR produced no text')
                    target.write_text(text)
                except (ImportError, OSError, ValueError, subprocess.CalledProcessError) as exc:
                    status = 'missing_extraction: ' + str(exc)
            if target.exists():
                self.add(eid, captured, captured, target.read_text(), 'document_text_unverified')
        else:
            status = 'unsupported_format'
        self.coverage.append({**item, 'status': status})

    def packet(self, request):
        cutoff = timestamp(request['audit_at'])
        supplied = request['email'].casefold()
        # Use current snapshot for linkage only; never assert it was state at an earlier cutoff.
        users = {r['user_id'] for r in self.identities if r['email'].casefold() == supplied}
        for row in self.history:
            if row['field_name'] == 'email' and supplied in (row['old_value'].casefold(), row['new_value'].casefold()):
                users.add(row['user_id'])
        history = [r for r in self.history if r['user_id'] in users and timestamp(r['event_time']) <= cutoff]
        ids = {supplied, *(u.casefold() for u in users)}
        for row in history:
            ids.update(v.casefold() for v in (row['old_value'], row['new_value']) if v)
        snapshots = [r for r in self.identities if r['user_id'] in users]
        for row in snapshots:
            if timestamp(row['updated_at']) <= cutoff:
                ids.update(v.casefold() for k, v in row.items() if k != 'updated_at' and v)
        candidate = set()
        for value in ids:
            # Token set intersection also handles identifiers containing spaces such as names/phones.
            terms = tokens(value)
            if terms:
                matches = [self.inverted.get(term, set()) for term in terms]
                candidate.update(set.intersection(*map(set, matches)))
        def eligible(i):
            u = self.units[i]
            return timestamp(u['captured_at']) <= cutoff and timestamp(u['observed_at']) <= cutoff
        candidate = {i for i in candidate if eligible(i)}
        # Follow only resource IDs grounded in eligible evidence; never use future evidence as a bridge.
        linked = set()
        for i in candidate:
            linked.update(resources(self.units[i]['content']))
        for rid in linked:
            candidate.update(i for i in self.by_resource[rid] if eligible(i))
        return dict(request=request, identity_user_ids=sorted(users), identity_history=history,
                    identity_snapshots_for_linkage_only=snapshots, rules=self.rules,
                    evidence=[self.units[i] for i in sorted(candidate)],
                    extraction_gaps=[r for r in self.coverage if r['status'] != 'extracted'])
