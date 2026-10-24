"""Plain data records shared across the pipeline."""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Request:
    case_id: str
    email: str
    requested_at: str
    reported_status: str
    reported_at: str
    audit_at: str
    text: str


@dataclass(frozen=True)
class Activity:
    activity_id: str
    time: str
    event_type: str
    source: str
    target: str
    resource_id: str
    result: str
    details: str


@dataclass
class Observation:
    """One fact about one resource copy, taken from one evidence file."""
    evidence_id: str
    captured_at: str          # file capture time: decides whether the file is usable at audit_at
    observed_at: str          # record time: decides which observation is latest
    system: str
    resource_id: str
    present: bool
    copy: str = "primary"
    links: frozenset = frozenset()   # identifier tokens tying the record to a person
    pii: bool = False                # direct identifiers still visible in the record
    attrs: dict = field(default_factory=dict)


@dataclass(frozen=True)
class Coverage:
    """A file that enumerates a whole collection: anything not listed is absent."""
    evidence_id: str
    captured_at: str
    observed_at: str
    system: str
    copy: str
    listed: frozenset


@dataclass(frozen=True)
class Step:
    phase: str
    system_id: str
    resource_id: str
    operation: str
    state: str
    evidence_id: str = ""
