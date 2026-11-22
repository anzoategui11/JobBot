"""Create the JobBot desktop shortcut via PowerShell's WScript.Shell COM object."""

import subprocess

from .config import ROOT, Config


def _ps_quote(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def make_shortcut(cfg: Config) -> str:
    target = ROOT / "JobBot.cmd"
    script = f"""
$desktop = [Environment]::GetFolderPath('Desktop')
$lnkPath = Join-Path $desktop 'JobBot.lnk'
$s = New-Object -ComObject WScript.Shell
$l = $s.CreateShortcut($lnkPath)
$l.TargetPath = {_ps_quote(str(target))}
$l.WorkingDirectory = {_ps_quote(str(ROOT))}
$l.IconLocation = {_ps_quote(str(cfg.brave_exe) + ",0")}
$l.WindowStyle = 7
$l.Description = 'Start JobBot (Brave job-hunting profile + helper)'
$l.Save()
Write-Output $lnkPath
"""
    out = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True, text=True, check=True,
    )
    return out.stdout.strip()
