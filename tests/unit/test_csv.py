import csv
import json

import pytest

from backend.app.core.config import Settings
from shared.contracts import ProcessingOptions
from worker.processors.csv_processor import PermanentInputError, process_csv


def run(tmp_path, content, options=None, **limits):
    source = tmp_path / "input.csv"
    source.write_bytes(content)
    output = tmp_path / "out"
    settings = Settings(storage_root=tmp_path, **limits)
    summary = process_csv(source, output, options or ProcessingOptions(), settings)
    with (output / "cleaned.csv").open(encoding="utf-8-sig", newline="") as f:
        cleaned = list(csv.reader(f))
    with (output / "errors.csv").open(encoding="utf-8-sig", newline="") as f:
        errors = list(csv.DictReader(f))
    return summary, cleaned, errors


def test_real_streaming_rules_and_safe_export(tmp_path):
    data = ("\ufeff ID , Email ,created_at,note\r\n"
            "1, a@example.test ,2026-01-01,=1+1\r\n"
            "2,bad,2026-01-02,hello\r\n"
            "1,dup@example.test,2026-01-03,duplicate\r\n"
            "3,c@example.test,invalid,hello\r\n"
            ",,,\r\n"
            '4,d@example.test,2026-01-04,"two\nlines"\r\n').encode("utf-8")
    options = ProcessingOptions(required_columns=["id", "email"], required_values=["id", "email"],
                                duplicate_columns=["id"], column_types={"id": "integer", "email": "email", "created_at": "date"})
    summary, cleaned, errors = run(tmp_path, data, options)
    assert summary["processed_rows"] == 6
    assert summary["written_rows"] == 2
    assert summary["error_rows"] == 3
    assert summary["duplicate_rows"] == 1
    assert summary["empty_rows_removed"] == 1
    assert summary["formula_cells_escaped"] == 1
    assert cleaned[1] == ["1", "a@example.test", "2026-01-01", "'=1+1"]
    assert cleaned[2][-1] == "two\nlines"
    assert {e["error_type"] for e in errors} == {"TYPE", "DUPLICATE"}
    assert [e["row_number"] for e in errors] == ["3", "4", "5"]
    assert json.loads((tmp_path / "out" / "summary.json").read_text())["export_policy"] == "apostrophe-prefix"


@pytest.mark.parametrize("data,code", [
    (b"a,a\n1,2\n", "INVALID_HEADER"), (b"\n", "INVALID_HEADER"),
    (b"a\n\xff\n", "INVALID_UTF8_OR_CSV"), (b"a\n\"unclosed", "INVALID_UTF8_OR_CSV"),
    (b"PK\x03\x04\x00zip", "ROW_TOO_LARGE_OR_BINARY"),
])
def test_invalid_file_policy(tmp_path, data, code):
    with pytest.raises(PermanentInputError, match=code):
        run(tmp_path, data)


def test_row_limit(tmp_path):
    with pytest.raises(PermanentInputError, match="ROW_LIMIT_EXCEEDED"):
        run(tmp_path, b"a\n1\n2\n", max_rows=1)


def test_cell_limit(tmp_path):
    with pytest.raises(PermanentInputError):
        run(tmp_path, b"a\n123456789\n", max_cell_chars=8)


def test_keep_invalid_and_duplicates(tmp_path):
    summary, cleaned, errors = run(tmp_path, b"id,n\n1,no\n1,2\n1,3\n", ProcessingOptions(
        duplicate_columns=["id"], column_types={"n": "integer"}, invalid_rows="keep", remove_duplicates=False))
    assert len(cleaned) == 4
    assert summary["error_rows"] == 2
    assert errors[-1]["error_type"] == "DUPLICATE"


def test_missing_rule_column(tmp_path):
    with pytest.raises(PermanentInputError, match="MISSING_RULE_COLUMNS"):
        run(tmp_path, b"a\n1\n", ProcessingOptions(required_values=["missing"]))


def test_raw_export_is_explicit(tmp_path):
    summary, cleaned, _ = run(tmp_path, b"a\n=1+1\n", ProcessingOptions(safe_export=False))
    assert cleaned[1] == ["=1+1"]
    assert summary["export_policy"] == "raw"
