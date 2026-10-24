import importlib.util
import json
import unittest
from pathlib import Path
spec=importlib.util.spec_from_file_location('evaluate_cli',Path(__file__).resolve().parents[1]/'evaluation/main.py')
module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


class EvaluationTests(unittest.TestCase):
    def test_missing_case_penalizes_status_and_sequence(self):
        gold={'c':dict(case_id='c',deletion_status='NOT_APPLICABLE',steps_json='[]')}
        result=module.evaluate(gold,{})
        self.assertEqual(result['coverage'],0)
        self.assertEqual(result['status_accuracy'],0)
        self.assertEqual(result['exact_row_and_sequence_accuracy'],0)

    def test_extra_case_is_rejected(self):
        with self.assertRaises(ValueError):
            module.evaluate({'a':{}},{'b':{}})

    def test_wrong_state_penalizes_fields_and_validity(self):
        step=dict(step_no=1,phase='VERIFICATION',operation='VERIFY_RECORD_ABSENT',state='ABSENT',system_id='account_service',resource_id='account_1',evidence_id='e1')
        gold={'c':dict(case_id='c',deletion_status='COMPLETE',steps_json=json.dumps([step]))}
        wrong=dict(step,state='PRESENT')
        pred={'c':dict(case_id='c',deletion_status='COMPLETE',steps_json=json.dumps([wrong]))}
        result=module.evaluate(gold,pred)
        self.assertEqual(result['status_accuracy'],1)
        self.assertEqual(result['schema_valid_rate'],0)
        self.assertEqual(result['exact_row_and_sequence_accuracy'],0)
        self.assertAlmostEqual(result['positional_field_accuracy'],6/7)


if __name__=='__main__': unittest.main()
