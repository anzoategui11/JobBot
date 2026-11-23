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
    sp = sub.add_parser("seed-profile", help="draft profile.md from your CV (never overwrites it)")
    sp.add_argument("--force", action="store_true", help="write profile.md even if it exists")
    sub.add_parser("profile-check", help="show which profile.md facts are still blank")
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
    elif args.cmd == "seed-profile":
        from .llm import get_backend
        from .seed import seed

        async def go():
            b = get_backend(cfg)
            try:
                return await seed(cfg, b, force=args.force)
            finally:
                if hasattr(b, "aclose"):
                    await b.aclose()
        path = asyncio.run(go())
        print(f"Wrote {path}")
        if path.name != "profile.md":
            print("profile.md already exists, so the draft went to this separate file. Copy what you want across.")
        _profile_check(path)
    elif args.cmd == "profile-check":
        _profile_check(cfg.data_dir / "profile.md")


def _profile_check(path) -> None:
    from . import profile
    if not path.exists():
        raise SystemExit(f"{path} doesn't exist yet - run seed-profile first.")
    prof = profile.load(path)
    blank = [(s.name, k) for s in prof.sections.values() for k, v, _ in s.entries.values() if not v]
    print(f"\n{path}: {len(prof.sections)} sections, "
          f"{sum(len(s.entries) for s in prof.sections.values())} facts, {len(blank)} blank.")
    if blank:
        print("Blank (fill in now, or JobBot will ask the first time a form needs them):")
        for section, key in blank:
            print(f"  [{section}] {key}")


if __name__ == "__main__":
    main()
