"""applications.csv: link normalisation, dedup, BOM/header, Excel-lock fallback to pending.csv."""

import csv
import ctypes
import sys
from datetime import date

import pytest

from jobbot import applog

ROW = {"Portal": "LinkedIn", "Company": "Initech", "Position": "ML Engineer",
       "Application link": "https://www.linkedin.com/jobs/view/123/?trk=abc", "CV used": "cv.pdf",
       "Location": "Buenos Aires", "Notes": "", "Role": "ML Engineer"}


def test_normalize_link():
    n = applog.normalize_link
    assert n("https://www.linkedin.com/jobs/view/4012345678/?refId=x&trackingId=y") == \
        "https://www.linkedin.com/jobs/view/4012345678/"
    assert n("https://www.linkedin.com/jobs/view/senior-ml-engineer-at-acme-4012345678") == \
        "https://www.linkedin.com/jobs/view/4012345678/"
    assert n("https://www.linkedin.com/jobs/search/?currentJobId=4012345678&keywords=ml") == \
        "https://www.linkedin.com/jobs/view/4012345678/"
    assert n("https://Boards.Greenhouse.io/acme/jobs/42/?gh_src=abc&utm_source=li#app") == \
        "https://boards.greenhouse.io/acme/jobs/42"
    assert n("https://jobs.acme.com/apply?id=7&utm_medium=x") == "https://jobs.acme.com/apply?id=7"


def test_log_writes_bom_header_and_dedups(tmp_path):
    log, pend = tmp_path / "applications.csv", tmp_path / "pending.csv"
    r, row = applog.log(log, ROW, pend, today=date(2026, 11, 23))
    assert r == "added" and row["Date"] == "11/23/2026" and row["Status"] == "Applied"
    raw = log.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and raw.count(b"\xef\xbb\xbf") == 1
    again = dict(ROW, **{"Application link": "https://www.linkedin.com/jobs/view/123"})
    r, existing = applog.log(log, again, pend)
    assert r == "duplicate" and existing["Date"] == "11/23/2026"
    applog.log(log, dict(ROW, **{"Application link": "https://acme.com/jobs/9", "Company": "Ácme"}), pend)
    with log.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == applog.COLUMNS and len(rows) == 3 and rows[2][2] == "Ácme"
    assert log.read_bytes().count(b"\xef\xbb\xbf") == 1


@pytest.mark.skipif(sys.platform != "win32", reason="Windows file locking")
@pytest.mark.parametrize("share", [0, 1])          # no sharing / others may read (what Excel does)
def test_locked_file_goes_to_pending_then_flushes(tmp_path, share):
    log, pend = tmp_path / "applications.csv", tmp_path / "pending.csv"
    applog.log(log, ROW, pend)
    k32 = ctypes.windll.kernel32
    k32.CreateFileW.restype = ctypes.c_void_p
    handle = k32.CreateFileW(str(log), 0x80000000, share, None, 3, 0x80, None)
    assert handle not in (None, ctypes.c_void_p(-1).value)
    try:
        r, _ = applog.log(log, dict(ROW, **{"Application link": "https://acme.com/jobs/1"}), pend)
        assert r == "pending" and pend.exists()
        r, _ = applog.log(log, dict(ROW, **{"Application link": "https://acme.com/jobs/1/"}), pend)
        assert r == "duplicate"                                               # pending rows count too
        assert applog.flush_pending(log, pend) == 0                           # still locked
    finally:
        k32.CloseHandle(ctypes.c_void_p(handle))
    assert applog.flush_pending(log, pend) == 1 and not pend.exists()
    with log.open(encoding="utf-8-sig", newline="") as f:
        assert [r["Application link"] for r in csv.DictReader(f)] == [ROW["Application link"],
                                                                      "https://acme.com/jobs/1"]


def test_old_log_gets_new_column_and_keeps_rows(tmp_path):
    log, pend = tmp_path / "applications.csv", tmp_path / "pending.csv"
    old = ("Date,Portal,Company,Position,Application link,Status,CV used,Location,Notes,Role\r\n"
           "11/23/2026,ChatGPT,EmiLabs,Implementation Engineer,https://jobs.lever.co/emilabs/1,Interview,cv.pdf,"
           "Buenos Aires,my note,AI Engineer\r\n")
    log.write_bytes(b"\xef\xbb\xbf" + old.encode("utf-8"))
    r, _ = applog.log(log, dict(ROW, **{"Expected salary": "USD 4000"}), pend)
    assert r == "added"
    with log.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert list(rows[0]) == applog.COLUMNS
    assert rows[0]["Portal"] == "ChatGPT" and rows[0]["Status"] == "Interview" and rows[0]["Expected salary"] == ""
    assert rows[1]["Expected salary"] == "USD 4000"
    assert log.read_bytes().count(b"\xef\xbb\xbf") == 1
