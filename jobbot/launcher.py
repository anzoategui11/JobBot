"""Start the dedicated Brave profile with remote debugging, or reuse it if already up."""

import asyncio
import json
import subprocess
import urllib.request

from .config import Config


def cdp_version(port: int, timeout: float = 1.0) -> dict | None:
    """Return Brave's /json/version info if the debugging port answers, else None."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as r:
            return json.load(r)
    except (OSError, ValueError):
        return None


async def ensure_brave(cfg: Config, wait_s: float = 20.0) -> bool:
    """Make sure Brave is listening on cfg.port. Returns True if we launched it."""
    if await asyncio.to_thread(cdp_version, cfg.port):
        return False

    cfg.profile_dir.mkdir(parents=True, exist_ok=True)
    subprocess.Popen([
        str(cfg.brave_exe),
        f"--remote-debugging-port={cfg.port}",
        f"--user-data-dir={cfg.profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
    ])

    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait_s
    while loop.time() < deadline:
        if await asyncio.to_thread(cdp_version, cfg.port):
            return True
        await asyncio.sleep(0.25)
    raise RuntimeError(
        f"Brave did not open its debugging port {cfg.port} within {wait_s:.0f}s. "
        "If a JobBot Brave window is already open (started some other way), close it and try again."
    )
