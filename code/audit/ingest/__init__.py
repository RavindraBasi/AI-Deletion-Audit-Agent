"""Evidence ingestion: every manifest file becomes Observations (+ Coverage for full dumps)."""
from functools import partial

from ..config import DATASET
from . import documents, logs, records, sql


def load_evidence(manifest, aliases, extract_image, root=DATASET):
    """Parse every file in the manifest. Returns (observations, coverages)."""
    parsers = {
        ".sql": sql.parse,
        ".json": records.parse_json,
        ".txt": records.parse_txt,
        ".log": logs.parse,
        ".pdf": partial(documents.parse_pdf, aliases=aliases),
        ".png": partial(documents.parse_image, extract=extract_image),
    }
    observations, coverages = [], []
    for entry in manifest:
        path = root / entry["file_path"]
        obs, cov = parsers[path.suffix](path, entry["file_id"], entry["captured_at"])
        observations += obs
        coverages += cov
    return observations, coverages
