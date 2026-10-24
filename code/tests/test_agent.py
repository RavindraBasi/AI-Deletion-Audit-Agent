"""Regression checks: python3 -m pytest code/tests -q"""
import json
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE))
sys.path.insert(0, str(CODE / "evaluation"))

import pytest  # noqa: E402

from audit import dataset  # noqa: E402
from audit.agent import DeletionAuditor  # noqa: E402
from audit.ingest.logs import parse_line  # noqa: E402
from audit.llm.client import LLM  # noqa: E402
from audit.output import serialize  # noqa: E402
from custom_cases import run_custom  # noqa: E402


@pytest.fixture(scope="module")
def results():
    requests = dataset.load_requests() + dataset.load_requests("sample_deletion_requests.csv")
    return DeletionAuditor(LLM(offline=True)).run(requests)


@pytest.mark.parametrize("name,got,want", run_custom())
def test_custom_scenarios(name, got, want):
    assert got == want, name


def resources(result):
    return {s.resource_id for s in result[1]}


def test_lifecycles_do_not_leak_between_requests_of_one_subject(results):
    assert "object_19" not in resources(results["case_07"])          # belongs to February's case_61
    assert "object_19" in resources(results["case_61"])
    assert not {"tombstone_17", "account_6523"} & resources(results["case_17"])   # March's case_46
    assert {"tombstone_17", "account_6523"} <= resources(results["case_46"])


def test_non_deletion_requests_are_not_applicable(results):
    for n in range(533, 543):
        assert results[f"case_{n}"] == ("NOT_APPLICABLE", [])


def test_replica_drift_is_reported(results):
    status, steps = results["case_16"]
    assert status == "INCOMPLETE"
    assert any(s.operation == "REPORT_REMAINING_RECORD" and s.resource_id == "account_994"
               and s.evidence_id == "evidence_05" for s in steps)


def test_serialization_matches_sample_format(results):
    expected = '[{"evidence_id":"evidence_54","operation":"VERIFY_RECORD_ABSENT","phase":"VERIFICATION",' \
               '"resource_id":"account_001","state":"ABSENT","step_no":1,"system_id":"account_service"}]'
    assert serialize(results["case_01"][1]) == expected
    assert json.loads(expected)


@pytest.mark.parametrize("line,service,trace,key", [
    ('2026-08-22T10:51:11Z <15>1 node-1 account_service tr-1 - - level=DEBUG event="profile_lookup_started" '
     'lookup_key="a@b.io"', "account_service", "tr-1", "lookup_key"),
    ('2026-07-28T05:51:18Z [DEBUG] [account_service] [tr-2] profile_lookup_started: lookup_key="a@b.io"',
     "account_service", "tr-2", "lookup_key"),
    ('2026-07-03T07:51:32Z | DEBUG | security_service | tr-3 | audit_record_read_completed | '
     'resource_id="audit_record_1" correlation_key="user_1"', "security_service", "tr-3", "correlation_key"),
    ('2026-09-30T11:51:03Z requestId=tr-4 level=DEBUG function=profile_service message="resource_id=\\"account_1\\" '
     'correlation_key=\\"a@b.io\\""', None, "tr-4", "correlation_key"),
    ('2026-09-10T08:56:17Z {"service":"account_service","trace_id":"tr-5","lookup_key":"a@b.io"}',
     "account_service", "tr-5", "lookup_key"),
])
def test_log_layouts_parse_to_one_shape(line, service, trace, key):
    fields = parse_line(line)
    assert fields.get("service") == service and fields["trace_id"] == trace and key in fields
