"""Your one job-hunting password, kept in Windows Credential Manager (JobBot / job-password).

It only ever goes from here straight into a password field on the page. Never printed, logged, shown in
the panel, saved to profile.md / OneDrive / applications.csv, or sent to the AI.
"""

import getpass
import sys

import keyring
from keyring.errors import KeyringError

SERVICE = "JobBot"
USER = "job-password"


def get() -> str | None:
    try:
        return keyring.get_password(SERVICE, USER)
    except KeyringError:
        return None


def set_interactive() -> None:
    """`python -m jobbot set-password`: hidden input, typed twice."""
    if not sys.stdin.isatty():
        raise SystemExit("Run this in a normal terminal window (PowerShell or cmd) so the password stays hidden.")
    first = getpass.getpass("Job-hunting password (hidden): ")
    if not first:
        raise SystemExit("Nothing entered. Nothing saved.")
    if getpass.getpass("Type it again: ") != first:
        raise SystemExit("The two entries don't match. Nothing saved.")
    if len(first) < 8:
        print("Note: many sites require at least 8 characters, plus a digit and a symbol.")
    keyring.set_password(SERVICE, USER, first)
    print("Saved to Windows Credential Manager (JobBot / job-password).")
