"""Bounded streaming CSV transformation. No database or Redis dependencies."""
import csv
import hashlib
import json
import math
import os
import re
from datetime import date

from shared.contracts import ProcessingOptions


class PermanentInputError(Exception):
    pass


class LostLease(Exception):
    pass


class Cancelled(Exception):
    pass


def normalize(name):
    return re.sub(r"\s+", "_", name.strip().lower())


def bounded_lines(stream, max_chars):
    # readline's limit also bounds a single hostile physical line before csv parses it.
    while line := stream.readline(max_chars + 1):
        if len(line) > max_chars or "\x00" in line:
            raise PermanentInputError("ROW_TOO_LARGE_OR_BINARY")
        yield line


def read_header(reader, options, settings):
    try:
        header = next(reader)
    except StopIteration:
        raise PermanentInputError("MISSING_HEADER") from None
    header = [normalize(x) if options.normalize_columns else x for x in header]
    if (not header or len(header) > settings.max_columns or any(not x or len(x) > 128 for x in header)
            or len(set(header)) != len(header)):
        raise PermanentInputError("INVALID_HEADER")
    referenced = set(options.required_columns + options.required_values + options.duplicate_columns)
    referenced.update(options.column_types)
    if not referenced.issubset(header):
        raise PermanentInputError("MISSING_RULE_COLUMNS")
    return header


def inspect_header(path, options, settings):
    csv.field_size_limit(settings.max_cell_chars)
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            reader = csv.reader(bounded_lines(f, settings.max_row_chars), strict=True)
            return read_header(reader, options, settings)
    except (UnicodeError, csv.Error) as exc:
        raise PermanentInputError("INVALID_UTF8_OR_CSV") from exc


def valid_value(value, kind):
    try:
        if kind == "integer":
            return bool(re.fullmatch(r"[+-]?\d+", value, flags=re.ASCII))
        if kind == "float":
            return math.isfinite(float(value))
        if kind == "date":
            return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value)) and bool(date.fromisoformat(value))
        if kind == "email":
            return len(value) <= 254 and bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))
    except (ValueError, OverflowError):
        return False
    return False


class BudgetWriter:
    def __init__(self, stream, budget):
        self.stream, self.budget = stream, budget

    def write(self, text):
        self.budget[0] -= len(text.encode("utf-8"))
        if self.budget[0] < 0:
            raise PermanentInputError("OUTPUT_LIMIT_EXCEEDED")
        return self.stream.write(text)


def process_csv(input_path, output_dir, options: ProcessingOptions, settings, checkpoint=lambda rows: None):
    output_dir.mkdir(parents=True, exist_ok=False)
    csv.field_size_limit(settings.max_cell_chars)
    counts = {"processed_rows": 0, "written_rows": 0, "error_rows": 0,
              "duplicate_rows": 0, "empty_rows_removed": 0, "formula_cells_escaped": 0}
    seen: set[bytes] = set()
    budget = [64 * 1024 * 1024 - 6]  # reserve the two UTF-8 BOMs; shared output ceiling

    def safe(value):
        if options.safe_export and value.lstrip(" \t\r\n").startswith(("=", "+", "-", "@")):
            counts["formula_cells_escaped"] += 1
            return "'" + value
        return value

    try:
        with (input_path.open("r", encoding="utf-8-sig", newline="") as source,
              (output_dir / "cleaned.csv").open("w", encoding="utf-8-sig", newline="") as cleaned,
              (output_dir / "errors.csv").open("w", encoding="utf-8-sig", newline="") as errors):
            reader = csv.reader(bounded_lines(source, settings.max_row_chars), strict=True)
            header = read_header(reader, options, settings)
            writer = csv.writer(BudgetWriter(cleaned, budget))
            reporter = csv.writer(BudgetWriter(errors, budget))
            writer.writerow([safe(x) for x in header])
            reporter.writerow(["row_number", "column", "error_type", "description"])
            duplicate_indexes = [header.index(x) for x in options.duplicate_columns]
            checkpoint(0)
            for number, raw in enumerate(reader, start=2):
                counts["processed_rows"] += 1
                if counts["processed_rows"] > settings.max_rows:
                    raise PermanentInputError("ROW_LIMIT_EXCEEDED")
                if sum(map(len, raw)) > settings.max_row_chars:
                    raise PermanentInputError("ROW_LIMIT_EXCEEDED")
                if number % 256 == 0:
                    checkpoint(counts["processed_rows"])
                values = [v.strip() if options.trim_whitespace else v for v in raw]
                if not any(v.strip() for v in values) and options.remove_empty_rows:
                    counts["empty_rows_removed"] += 1
                    continue
                issues = []
                if len(values) != len(header):
                    issues.append(("", "COLUMN_COUNT", "Row width does not match header"))
                else:
                    row = dict(zip(header, values, strict=True))
                    for column in options.required_values:
                        if not row[column]:
                            issues.append((column, "REQUIRED", "Required value is missing"))
                    for column, kind in options.column_types.items():
                        if row[column] and not valid_value(row[column], kind):
                            issues.append((column, "TYPE", f"Expected {kind}"))
                duplicate = False
                # Invalid rows do not reserve a duplicate key; first valid row wins.
                if not issues:
                    identity = [values[i] for i in duplicate_indexes] if duplicate_indexes else values
                    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).digest()
                    duplicate = digest in seen
                    if duplicate:
                        counts["duplicate_rows"] += 1
                        issues.append((",".join(options.duplicate_columns), "DUPLICATE", "Duplicate key or row"))
                    else:
                        seen.add(digest)  # bounded to max_rows entries of 32 bytes + Python set overhead
                if issues:
                    counts["error_rows"] += 1
                    for column, kind, message in issues:
                        reporter.writerow([number, safe(column), kind, message])
                exclude = (duplicate and options.remove_duplicates) or (
                    bool(issues) and not duplicate and options.invalid_rows == "exclude")
                if not exclude:
                    writer.writerow([safe(x) for x in values])
                    counts["written_rows"] += 1
            checkpoint(counts["processed_rows"])
            for f in (cleaned, errors):
                f.flush()
                os.fsync(f.fileno())
        summary = {**counts, "total_rows": counts["processed_rows"],
                   "options": options.model_dump(), "export_policy": "apostrophe-prefix" if options.safe_export else "raw",
                   "row_number_policy": "logical CSV record; header=1", "duplicate_policy": "first valid row wins"}
        with (output_dir / "summary.json").open("w", encoding="utf-8") as output:
            BudgetWriter(output, budget).write(json.dumps(summary, ensure_ascii=False, indent=2))
            output.flush()
            os.fsync(output.fileno())
        return summary
    except (csv.Error, UnicodeError) as exc:
        raise PermanentInputError("INVALID_UTF8_OR_CSV") from exc
