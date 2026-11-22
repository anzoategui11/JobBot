import argparse
import asyncio

from . import config


def main() -> None:
    ap = argparse.ArgumentParser(prog="jobbot")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("run", help="start/attach Brave and listen for Ctrl+Alt+G")
    sub.add_parser("make-shortcut", help="create the JobBot desktop shortcut")
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


if __name__ == "__main__":
    main()
