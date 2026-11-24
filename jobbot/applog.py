"""applications.csv: one row per application, append-only, deduplicated by application link.

UTF-8 with BOM so Excel shows accents. If Excel has the file open (it locks it), rows wait in
pending.csv in the code folder and are written on the next log or the next start.
"""

import csv
import io
import re
from datetime import date
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .config import ROOT

COLUMNS = ["Date", "Portal", "Company", "Position", "Application link", "Status", "CV used", "Location",
           "Notes", "Role", "Expected salary"]
PENDING = ROOT / "pending.csv"
_TRACKING = re.compile(r"^(utm_|trk|ref|refid|trackingid|tracking_id|src|source|gh_src|lever-|"
                       r"ebp|fbclid|gclid|mc_|_hs|origin)", re.I)


def normalize_link(url: str) -> str:
    """Same job -> same link: no tracking params or fragment; LinkedIn jobs -> /jobs/view/<id>/."""
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = (parts.hostname or "").lower()
    if host.endswith("linkedin.com"):
        m = re.search(r"/jobs/view/(?:[^/]*?-)?(\d+)", parts.path)
        job = m.group(1) if m else dict(parse_qsl(parts.query)).get("currentJobId", "")
        if job.isdigit():
            return f"https://www.linkedin.com/jobs/view/{job}/"
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not _TRACKING.match(k)])
    path = parts.path.rstrip("/") or "/"
    netloc = host + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme.lower() or "https", netloc, path, query, ""))


def _read_rows(path: Path) -> list[dict]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            return list(csv.DictReader(f))
    except (FileNotFoundError, PermissionError):     # locked: flush_pending re-checks duplicates later
        return []


def _upgrade_header(path: Path) -> None:
    """A log made before a column was added gets the new header; existing rows keep their values (new column empty)."""
    try:
        with path.open(encoding="utf-8-sig", newline="") as f:
            header = next(csv.reader(f), None)
    except (FileNotFoundError, PermissionError):
        return
    if not header or header == COLUMNS or not set(header) <= set(COLUMNS):
        return                                        # empty, current, or columns we don't know: leave it alone
    rows = _read_rows(path)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\r\n")
    w.writeheader()
    w.writerows(rows)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(buf.getvalue(), encoding="utf-8-sig", newline="")
    tmp.replace(path)                                 # raises PermissionError while Excel has it open


def _write(path: Path, rows: list[dict]) -> None:
    """Append rows; header + BOM only when the file is new. Raises PermissionError when Excel has it open."""
    new = not path.exists() or path.stat().st_size == 0
    if not new:
        _upgrade_header(path)
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\r\n")
    if new:
        w.writeheader()
    w.writerows(rows)
    with path.open("a", encoding="utf-8-sig" if new else "utf-8", newline="") as f:
        f.write(buf.getvalue())                      # one write: no half rows if something fails


def flush_pending(path: Path, pending: Path = PENDING) -> int:
    """Move rows waiting in pending.csv into the log. Returns how many were written (0 if still locked)."""
    rows = _read_rows(pending)
    if not rows:
        return 0
    have = {normalize_link(r.get("Application link", "")) for r in _read_rows(path)}
    fresh = [r for r in rows if normalize_link(r.get("Application link", "")) not in have]
    try:
        if fresh:
            _write(path, fresh)
    except PermissionError:
        return 0
    pending.unlink(missing_ok=True)
    return len(fresh)


def log(path: Path, row: dict, pending: Path = PENDING, today: date | None = None) -> tuple[str, dict]:
    """Append one application. Returns ("added" | "duplicate" | "pending", the row written or found)."""
    flush_pending(path, pending)
    row = {k: " ".join(str(row.get(k, "")).split())[:500] for k in COLUMNS}
    row["Date"] = (today or date.today()).strftime("%m/%d/%Y")
    row["Status"] = "Applied"
    key = normalize_link(row["Application link"])
    for existing in _read_rows(path) + _read_rows(pending):
        if normalize_link(existing.get("Application link", "")) == key:
            return "duplicate", existing
    try:
        _write(path, [row])
        return "added", row
    except PermissionError:
        _write(pending, [row])
        return "pending", row
