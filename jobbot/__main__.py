import argparse
import asyncio

from . import config


def main() -> None:
    ap = argparse.ArgumentParser(prog="jobbot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="start/attach Brave and listen for Ctrl+Alt+G")
    sub.add_parser("make-shortcut", help="create the JobBot desktop shortcut")
    sub.add_parser("llm-selftest", help="check the AI backend works and is locked down")
    sub.add_parser("set-groq-key", help="store your GroqCloud API key in Windows Credential Manager")
    args = ap.parse_args()

    cfg = config.load()
    if args.cmd == "run":
        from .browser import run
        try:
            asyncio.run(run(cfg))
        except KeyboardInterrupt:
            pass
    elif args.cmd == "make-shortcut":
        from .shortcut import make_shortcut
        print(f"Created {make_shortcut(cfg)}")
    elif args.cmd == "llm-selftest":
        from .llm.selftest import run_selftest
        raise SystemExit(0 if asyncio.run(run_selftest(cfg)) else 1)
    elif args.cmd == "set-groq-key":
        import getpass
        from .llm.groq import set_api_key
        key = getpass.getpass("Paste your Groq API key (input is hidden), then press Enter: ").strip()
        if not key:
            raise SystemExit("Nothing entered. Nothing saved.")
        if not key.startswith("gsk_") and input("Groq keys usually start with gsk_. Save anyway? [y/N] ").lower() != "y":
            raise SystemExit("Nothing saved.")
        set_api_key(key)
        print("Saved to Windows Credential Manager (JobBot / groq-api-key).")


if __name__ == "__main__":
    main()
