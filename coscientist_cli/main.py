"""coscientist CLI. Designed to run inside the Docker container, but works on
the host too if dependencies are installed.

Usage:
  coscientist research --task "..."
  coscientist evolve   --command "..."
  coscientist api
  coscientist users create <display_name>
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
        subprocess.check_call(
            [
                "git",
                "checkout",
                "v0",
                "--",
                "scaffold",
                "evolution",
                "research",
                "roles",
                "prompts",
                "tools",
                "api",
            ],
            cwd=str(settings.ROOT),
        )
        print("restored v0 sources; state/ and researcher_data/ preserved")
    except subprocess.CalledProcessError as e:
        print(f"reset failed (does v0 tag exist?): {e}", file=sys.stderr)
        sys.exit(1)


def cmd_users_create(args):
    from api import auth
    from api.tenancy import ensure_user_root

    user, api_key = auth.create_user(args.display_name)
    root = ensure_user_root(user.user_id)
    print(f"user_id: {user.user_id}")
    print(f"display_name: {user.display_name}")
    print(f"root: {root}")
    print("")
    print("API key (shown once):")
    print(api_key)


def cmd_users_list(args):
    from api import auth
    from api.tenancy import user_root

    users = auth.list_users(include_disabled=True)
    if not users:
        print("no users")
        return
    for user in users:
        status = "disabled" if user.disabled else "active"
        print(f"{user.user_id}\t{status}\t{user.display_name}\t{user_root(user.user_id)}")


def cmd_users_disable(args):
    from api import auth

    if not auth.set_disabled(args.user_id, True):
        print(f"no such user {args.user_id}", file=sys.stderr)
        sys.exit(1)
    print(f"disabled {args.user_id}")


def cmd_users_enable(args):
    from api import auth

    if not auth.set_disabled(args.user_id, False):
        print(f"no such user {args.user_id}", file=sys.stderr)
        sys.exit(1)
    print(f"enabled {args.user_id}")


def cmd_users_delete(args):
    from api import auth
    from api.tenancy import user_base_dir

    if not args.yes:
        print(
            "Refusing to delete without --yes. This removes the auth row and all "
            f"state under {user_base_dir(args.user_id)}.",
            file=sys.stderr,
        )
        sys.exit(2)

    base = user_base_dir(args.user_id)
    if not auth.delete_user(args.user_id):
        print(f"no such user {args.user_id}", file=sys.stderr)
        sys.exit(1)

    if base.exists():
        shutil.rmtree(base)
    print(f"deleted {args.user_id}")


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

    users = sub.add_parser("users", help="manage API-key users")
    users_sub = users.add_subparsers(dest="users_cmd", required=True)

    ucreate = users_sub.add_parser("create", help="create a user and print a one-time API key")
    ucreate.add_argument("display_name")
    ucreate.set_defaults(func=cmd_users_create)

    ulist = users_sub.add_parser("list", help="list users")
    ulist.set_defaults(func=cmd_users_list)

    udisable = users_sub.add_parser("disable", help="disable a user API key")
    udisable.add_argument("user_id")
    udisable.set_defaults(func=cmd_users_disable)

    uenable = users_sub.add_parser("enable", help="re-enable a disabled user")
    uenable.add_argument("user_id")
    uenable.set_defaults(func=cmd_users_enable)

    udelete = users_sub.add_parser(
        "delete",
        help="delete a user, their API key DB row, and all per-user state",
    )
    udelete.add_argument("user_id")
    udelete.add_argument(
        "--yes",
        action="store_true",
        help="confirm destructive deletion of the user and all state",
    )
    udelete.set_defaults(func=cmd_users_delete)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
