import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ghost_audit.pipeline import EvidenceIndex, resources
from ghost_audit.contract import validate


def write_csv(path,rows):
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name); (self.root/'evidence').mkdir()
        write_csv(self.root/'user_identities.csv',[dict(user_id='user_1',name='Test Person',email='new@example.org',phone='',email_hash='',updated_at='2026-01-01T00:00:00Z')])
        write_csv(self.root/'user_audit_log.csv',[dict(audit_id='audit_1',user_id='user_1',event_time='2025-12-01T00:00:00Z',field_name='email',old_value='old@example.org',new_value='new@example.org')])
        write_csv(self.root/'deletion_rules.csv',[dict(rule_id='r1',data_category='USER_PROFILE',condition='Always',required_action='DELETE')])
        write_csv(self.root/'system_activity.csv',[dict(activity_id='a1',event_time='2026-01-02T00:00:00Z',resource_id='analytics_1',details='old@example.org',reported_result='SUCCESS')])
        rows=[dict(resource_id='analytics_1',subject_reference='old@example.org',result='IDENTIFIABLE'),
              dict(resource_id='analytics_1',subject_reference='',result='ANONYMIZED')]
        (self.root/'evidence/a.json').write_text(json.dumps({'records':rows}))
        (self.root/'evidence/future.json').write_text(json.dumps({'records':[dict(resource_id='account_future',subject_reference='old@example.org')]}))
        write_csv(self.root/'evidence_files.csv',[
            dict(file_id='e1',file_path='evidence/a.json',origin='test',captured_at='2026-01-03T00:00:00Z'),
            dict(file_id='e2',file_path='evidence/future.json',origin='test',captured_at='2027-01-01T00:00:00Z')])
        self.index=EvidenceIndex(self.root,self.root/'cache')
        self.request=dict(case_id='test_1',email='old@example.org',audit_at='2026-02-01T00:00:00Z',request_text='Delete my account')

    def test_historical_email_resolves_user(self):
        self.assertEqual(self.index.packet(self.request)['identity_user_ids'],['user_1'])

    def test_future_evidence_cannot_enter_packet(self):
        packet=self.index.packet(self.request)
        self.assertNotIn('e2',{r['evidence_id'] for r in packet['evidence']})
        self.assertNotIn('account_future',json.dumps(packet))

    def test_anonymized_record_linked_by_resource(self):
        packet=self.index.packet(self.request)
        self.assertTrue(any('ANONYMIZED' in json.dumps(u['content']) for u in packet['evidence']))

    def test_missing_evidence_is_not_complete(self):
        with self.assertRaises(ValueError):
            validate(dict(case_id='test',deletion_status='COMPLETE',steps_json=[]))

    def test_non_deletion_empty_array(self):
        self.assertEqual(validate(dict(case_id='test',deletion_status='NOT_APPLICABLE',steps_json='[]')),[])

    def test_service_names_are_not_resource_bridges(self):
        self.assertEqual(resources({"system":"account_service","resource_id":"account_123"}), {"account_123"})

    def test_prefix_identifier_does_not_match(self):
        self.request['email']='user_10'
        packet=self.index.packet(self.request)
        self.assertEqual(packet['identity_user_ids'],[])
        self.assertEqual(packet['evidence'],[])


if __name__=='__main__': unittest.main()
