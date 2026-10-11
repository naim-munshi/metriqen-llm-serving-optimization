"""Deterministic reference-answer evaluation; no LLM-as-judge or semantic claims."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys
import unicodedata

MAX_BYTES = 2 * 1024 * 1024
MAX_ROWS = 10000

def load(path: Path, field: str) -> tuple[dict[str, str], str]:
    raw = path.read_bytes()
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("input must be nonempty and at most 2 MiB")
    rows: dict[str, str] = {}
    for number, line in enumerate(raw.splitlines(), 1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except (ValueError, UnicodeDecodeError) as exc:
            raise ValueError(f"invalid JSON on line {number}") from exc
        if not isinstance(item, dict) or set(item) != {"id", field}:
            raise ValueError(f"line {number} requires only id and {field}")
        key, value = item["id"], item[field]
        if (not isinstance(key, str) or not key.strip() or len(key) > 128
                or not isinstance(value, str) or len(value) > 8192):
            raise ValueError(f"invalid record on line {number}")
        if key in rows:
            raise ValueError(f"duplicate example ID: {key}")
        rows[key] = value
        if len(rows) > MAX_ROWS:
            raise ValueError("too many examples")
    if not rows:
        raise ValueError("input contains no examples")
    return rows, hashlib.sha256(raw).hexdigest()

def normalize(text: str) -> str:
    """Unicode NFC, casefold and whitespace collapse. No punctuation stripping."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).casefold()).strip()

def evaluate(references: dict[str, str], predictions: dict[str, str]) -> dict:
    if set(references) != set(predictions):
        missing = len(set(references) - set(predictions))
        extra = len(set(predictions) - set(references))
        raise ValueError(f"example IDs mismatch: {missing} missing, {extra} unexpected")
    total = len(references)
    exact = sum(references[key] == predictions[key] for key in references)
    normalized = sum(normalize(references[key]) == normalize(predictions[key]) for key in references)
    return {
        "sample_count": total,
        "exact_match": {"correct": exact, "rate": exact / total},
        "normalized_exact_match": {"correct": normalized, "rate": normalized / total},
        "limitations": [
            "String matches are narrow proxies, not semantic equivalence or factual correctness",
            "No model-generated grading, hallucination detection or safety evaluation",
            "Reference and prediction strings are never exported to result artifacts",
        ],
    }

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate NLP reference answers without storing content")
    parser.add_argument("--references", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if len({args.references.resolve(), args.predictions.resolve(), args.output.resolve()}) != 3:
            raise ValueError("input and output paths must be different")
        refs, refs_hash = load(args.references, "reference")
        preds, preds_hash = load(args.predictions, "prediction")
        result = {
            "schema_version": 1,
            "metric_version": "normalized-match-v1",
            "references_sha256": refs_hash,
            "predictions_sha256": preds_hash,
            "results": evaluate(refs, preds),
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"Evaluated {len(refs)} examples; report: {args.output}")
        return 0
    except (OSError, ValueError) as exc:
        print(f"evaluation error: {exc}", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
