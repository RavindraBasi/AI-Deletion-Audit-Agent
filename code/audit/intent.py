"""Does the request text authorize deletion? Claude (batched, cached) with a keyword fallback."""
import re

from .config import INTENT_BATCH_SIZE, INTENT_MODEL, PROMPTS_DIR

SCHEMA = {"type": "object", "properties": {"not_deletion": {"type": "array", "items": {"type": "integer"}}},
          "required": ["not_deletion"], "additionalProperties": False}

DELETION = re.compile(
    r"delet|eras|remov|clos(e|ing) (my|this|the) account|account (was|is) (fully )?(removed|gone)|profile is gone|"
    r"supprim|suppression|elimina|exclus|삭제|削除|löschen", re.I)
NOT_DELETION = re.compile(
    r"don.?t delete|do not delete|not a request to|lists only|promotional|copy of (the information|last)|download my|"
    r"portable format|misspelled|out of date|change the email|reset|what personal information|"
    r"삭제하지|하지 마|削除しないで|no (elimin|borr)|ne (supprimez|pas supprimer)|não (exclua|apague)", re.I)


def keyword_authorizes(text):
    return bool(DELETION.search(text)) and not NOT_DELETION.search(text)


def classify(texts, llm):
    """Map each distinct text → True when it authorizes deletion."""
    distinct = sorted(set(texts))
    system = (PROMPTS_DIR / "intent.md").read_text(encoding="utf-8")
    decided = {}
    for start in range(0, len(distinct), INTENT_BATCH_SIZE):
        batch = distinct[start:start + INTENT_BATCH_SIZE]
        numbered = "\n".join(f"{i}. {' '.join(t.split())}" for i, t in enumerate(batch, 1))
        answer = llm.json("request intent", INTENT_MODEL, system, numbered, SCHEMA, max_tokens=1024)
        if answer is None:   # offline and uncached
            decided.update({t: keyword_authorizes(t) for t in batch})
            continue
        rejected = set(answer["not_deletion"])
        if not rejected <= set(range(1, len(batch) + 1)):
            raise ValueError(f"intent model returned out-of-range indices: {sorted(rejected)}")
        decided.update({t: i not in rejected for i, t in enumerate(batch, 1)})
    for text, verdict in decided.items():   # cross-check: surface every disagreement for review
        if verdict != keyword_authorizes(text):
            print(f"[intent] model={'delete' if verdict else 'not-delete'} keyword disagrees: {text[:90]!r}")
    return decided
