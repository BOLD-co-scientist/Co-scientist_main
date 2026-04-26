"""coscientist CLI. Designed to run inside the Docker container, but works on
the host too if dependencies are installed.

Usage:
  coscientist research --task "..."
  coscientist evolve   --command "..."
  coscientist api
  coscientist status   <session_id>
  coscientist reset    --to-v0
"""
from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
import time

from scaffold import settings


def cmd_research(args):
    from research.runtime import run_session
    sid = args.session or _new_sid()
    asyncio.run(run_session(sid, args.task))
    print(f"session: {sid}")


def cmd_evolve(args):
    from evolution.runtime import run_command
    sid = args.session or ("evo-" + _new_sid())
    asyncio.run(run_command(sid, args.command))
    print(f"session: {sid}")


def cmd_api(args):
    import uvicorn
    uvicorn.run("api.server:app", host="0.0.0.0", port=8765, reload=False)


def cmd_status(args):
    sd = settings.session_dir(args.session_id)
    if not sd.exists():
        print(f"no such session {args.session_id}", file=sys.stderr)
        sys.exit(1)
    events = sd / "events.jsonl"
    if events.exists():
        with open(events) as f:
            tail = f.readlines()[-30:]
        print("".join(tail))


def cmd_reset(args):
    if not args.to_v0:
        print("only --to-v0 supported", file=sys.stderr)
        sys.exit(2)
    # Soft reset by checking out v0 tag if present.
    try:
        subprocess.check_call(["git", "checkout", "v0", "--", "scaffold", "evolution", "research", "roles", "prompts", "tools", "api"], cwd=str(settings.ROOT))
        print("restored v0 sources; state/ and researcher_data/ preserved")
    except subprocess.CalledProcessError as e:
        print(f"reset failed (does v0 tag exist?): {e}", file=sys.stderr)
        sys.exit(1)


def _new_sid() -> str:
    import uuid
    return time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]


def main():
    p = argparse.ArgumentParser(prog="coscientist")
    sub = p.add_subparsers(dest="cmd", required=True)

    pr = sub.add_parser("research", help="start a research session")
    pr.add_argument("--task", required=True)
    pr.add_argument("--session")
    pr.set_defaults(func=cmd_research)

    pe = sub.add_parser("evolve", help="run one evolution command")
    pe.add_argument("--command", required=True)
    pe.add_argument("--session")
    pe.set_defaults(func=cmd_evolve)

    pa = sub.add_parser("api", help="run the FastAPI server")
    pa.set_defaults(func=cmd_api)

    ps = sub.add_parser("status", help="tail session events")
    ps.add_argument("session_id")
    ps.set_defaults(func=cmd_status)

    pre = sub.add_parser("reset", help="reset code to a tag while preserving state/")
    pre.add_argument("--to-v0", action="store_true")
    pre.set_defaults(func=cmd_reset)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
