"""CSV reading and row validation.

Blank rows (including rows whose cells are all empty, as Excel produces) are
skipped wherever they appear. Duplicate policy: the first *valid* occurrence
of a normalized email wins; later occurrences are rejected as duplicates.
"""
from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from pathlib import Path

from .emailutil import normalize_email
from .template import norm_key

BREAK_CHARS = frozenset("\r\n\x0b\x0c\x1c\x1d\x1e\x85  ")
_ALT_DELIMS = {";": "semicolon", "\t": "tab", "|": "pipe", ",": "comma"}


class IngestError(Exception):
    pass


@dataclass
class Record:
    recno: int
    line: int
    fields: list[str]


@dataclass
class Table:
    header: list[str]
    email_idx: int
    records: list[Record]


@dataclass
class Accepted:
    email: str
    data: dict[str, str]
    recno: int
    line: int


@dataclass
class Reject:
    recno: int
    line: int
    reason: str
    fields: list[str]


def _is_blank(row: list[str]) -> bool:
    return all(not c.strip() for c in row)


def _check_header(header: list[str], delimiter: str) -> None:
    if len(header) == 1:
        for alt, name in _ALT_DELIMS.items():
            if alt != delimiter and alt in header[0]:
                raise IngestError(
                    f"header looks {name}-delimited but the delimiter is {delimiter!r}; "
                    f"re-run with --delimiter {alt!r}"
                )
    problems: list[str] = []
    seen: dict[str, str] = {}
    dups: list[str] = []
    for pos, name in enumerate(header, 1):
        if not name.strip():
            problems.append(f"empty column name at position {pos}")
            continue
        if name != name.strip():
            problems.append(f"column {name!r} has leading/trailing whitespace")
        key = norm_key(name)
        if key in seen:
            dups.append(name.strip())
        seen.setdefault(key, name)
    if dups:
        problems.append("duplicate column name(s): " + ", ".join(repr(d) for d in dups))
    if problems:
        raise IngestError("invalid CSV header: " + "; ".join(problems))


def read_table(path: str | Path, email_column: str, delimiter: str = ",") -> Table:
    raw = Path(path).read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise IngestError(
            f"{path} is not valid UTF-8 (invalid byte at offset {exc.start}); "
            "re-save it as 'CSV UTF-8'"
        )
    reader = csv.reader(io.StringIO(text, newline=""), delimiter=delimiter)
    header: list[str] | None = None
    records: list[Record] = []
    prev_line = 0
    recno = 0
    try:
        for row in reader:
            start = prev_line + 1
            prev_line = reader.line_num
            if _is_blank(row):
                continue
            if header is None:
                header = row
                continue
            recno += 1
            records.append(Record(recno, start, row))
    except csv.Error as exc:
        raise IngestError(f"CSV parse error near line {reader.line_num}: {exc}")
    if header is None:
        raise IngestError("the CSV is empty (no header row)")
    _check_header(header, delimiter)
    want = norm_key(email_column)
    idx = [i for i, h in enumerate(header) if norm_key(h) == want]
    if not idx:
        raise IngestError(
            f"email column {email_column!r} not found in header ({', '.join(header)})"
        )
    if not records:
        raise IngestError("the CSV has a header but no data rows")
    return Table(header, idx[0], records)


def validate_records(
    table: Table, required_cols: set[str], header_cols: set[str]
) -> tuple[list[Accepted], list[Reject]]:
    """required_cols: normalized column names referenced by template tags.
    header_cols: subset used in header values (subject), which must not contain line breaks."""
    header = table.header
    by_key = {norm_key(h): i for i, h in enumerate(header)}
    req_idx = [(by_key[k], header[by_key[k]]) for k in sorted(required_cols) if k in by_key]
    hdr_idx = [(by_key[k], header[by_key[k]]) for k in sorted(header_cols) if k in by_key]
    accepted: list[Accepted] = []
    rejects: list[Reject] = []
    first_line: dict[str, int] = {}
    for rec in table.records:
        if len(rec.fields) != len(header):
            rejects.append(
                Reject(rec.recno, rec.line,
                       f"field count mismatch (expected {len(header)}, got {len(rec.fields)})",
                       rec.fields)
            )
            continue
        fields = [c.strip() for c in rec.fields]
        email, err = normalize_email(fields[table.email_idx])
        if err:
            rejects.append(Reject(rec.recno, rec.line, err, rec.fields))
            continue
        empty = [name for i, name in req_idx if not fields[i]]
        if empty:
            rejects.append(Reject(rec.recno, rec.line, "empty field: " + ", ".join(empty), rec.fields))
            continue
        bad = [name for i, name in hdr_idx if any(c in BREAK_CHARS for c in fields[i])]
        if bad:
            rejects.append(
                Reject(rec.recno, rec.line, "line break in header value: " + ", ".join(bad), rec.fields)
            )
            continue
        if email in first_line:
            rejects.append(
                Reject(rec.recno, rec.line, f"duplicate of line {first_line[email]}", rec.fields)
            )
            continue
        first_line[email] = rec.line
        accepted.append(Accepted(email, dict(zip(header, fields)), rec.recno, rec.line))
    return accepted, rejects


def write_rejects(path: str | Path, header: list[str], rejects: list[Reject]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["record", "line", "reason", *header])
        for r in rejects:
            w.writerow([r.recno, r.line, r.reason, *r.fields])
