import json
import shutil
import time
from pathlib import Path

import requests
import streamlit as st

# Docker internal networking routes this to the API container
API_URL = "http://coscientist-api:8765"
STATE_DIR = Path("/app/state/sessions")

st.set_page_config(page_title="Coscientist Chat", layout="wide")

# Hide the Streamlit running animation
st.markdown(
    """
    <style>
        div[data-testid="stStatusWidget"] {
            visibility: hidden;
            display: none;
        }
    </style>
    """,
    unsafe_allow_html=True,
)

# Initialize session state variables
if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "skip_delete_confirm" not in st.session_state:
    st.session_state.skip_delete_confirm = False
if "mode" not in st.session_state:
    st.session_state.mode = "Research"


def is_evolution_session(sid: str) -> bool:
    return sid.startswith("evo-")


# --- POP-UP MODALS ---
@st.dialog("Delete Session?")
def confirm_deletion(session_id):
    st.warning(
        f"Are you sure you want to delete session **{session_id}**? This action cannot be undone."
    )

    skip_future = st.checkbox("Don't ask me again")

    col1, col2 = st.columns(2)
    if col1.button("Accept", type="primary", use_container_width=True):
        if skip_future:
            st.session_state.skip_delete_confirm = True

        # Delete directory from the filesystem
        session_path = STATE_DIR / session_id
        if session_path.exists():
            shutil.rmtree(session_path)

        # If the deleted session was currently open, clear the main screen
        if st.session_state.session_id == session_id:
            st.session_state.session_id = None

        st.rerun()

    if col2.button("Cancel", use_container_width=True):
        st.rerun()


# --- SIDEBAR: Navigation & HITL ---
is_awaiting_human = False  # Used to trigger Green status

# --- TOP-OF-PAGE MODE TOGGLE ---
# Rendered before the sidebar so it sits at the very top of the main viewport.
_mode_options = ["Research", "Evolution"]
_current_mode_idx = _mode_options.index(st.session_state.mode)
_toggle_col, _spacer = st.columns([0.35, 0.65])
with _toggle_col:
    if hasattr(st, "segmented_control"):
        new_mode = st.segmented_control(
            "Mode",
            _mode_options,
            default=st.session_state.mode,
            label_visibility="collapsed",
        )
    else:
        new_mode = st.radio(
            "Mode",
            _mode_options,
            index=_current_mode_idx,
            horizontal=True,
            label_visibility="collapsed",
        )
if new_mode and new_mode != st.session_state.mode:
    st.session_state.mode = new_mode
    st.session_state.session_id = None
    st.rerun()

with st.sidebar:
    # 1. TOP: New Session
    new_label = (
        "➕ New Research" if st.session_state.mode == "Research" else "➕ New Command"
    )
    if st.button(new_label, type="primary", use_container_width=True):
        st.session_state.session_id = None
        st.rerun()

    st.divider()

    # 1b. LIBRARY (persistent, shared across all sessions)
    st.header("Library")
    try:
        files_resp = requests.get(f"{API_URL}/library/files", timeout=5)
        library_files = files_resp.json() if files_resp.status_code == 200 else []
    except requests.exceptions.RequestException:
        library_files = []

    uploaded = st.file_uploader(
        "Upload",
        accept_multiple_files=True,
        label_visibility="collapsed",
        key=f"lib_uploader_{len(library_files)}",  # reset widget after each upload
    )
    if uploaded:
        for f in uploaded:
            try:
                requests.post(
                    f"{API_URL}/library/files",
                    files={"file": (f.name, f.getvalue())},
                    timeout=600,
                )
            except requests.exceptions.RequestException as e:
                st.error(f"Upload failed for {f.name}: {e}")
        st.rerun()

    st.caption(
        "Files >5 GB? Drop them into `./state/library/` on the host — they'll appear here."
    )

    if library_files:
        with st.container(height=180, border=False):
            for lf in library_files:
                col_l, col_r = st.columns([0.85, 0.15], vertical_alignment="center")
                size_kb = lf["size"] / 1024
                size_str = (
                    f"{size_kb / 1024:.1f} MB"
                    if size_kb >= 1024
                    else f"{size_kb:.1f} KB" if size_kb >= 1 else f"{lf['size']} B"
                )
                col_l.markdown(
                    f"📎 **{lf['name']}**  \n<span style='color:#888;font-size:11px'>{size_str}</span>",
                    unsafe_allow_html=True,
                )
                if col_r.button(
                    "🗑️", key=f"lib_del_{lf['name']}", help=f"Delete {lf['name']}"
                ):
                    try:
                        requests.delete(
                            f"{API_URL}/library/files/{lf['name']}", timeout=10
                        )
                    except requests.exceptions.RequestException:
                        pass
                    st.rerun()
    else:
        st.caption("No files in library yet.")

    # Health badge
    try:
        health = requests.get(f"{API_URL}/library/health", timeout=5).json()
        total_gb = health["total_bytes"] / (1024**3)
        free_gb = health["disk_free_bytes"] / (1024**3)
        warn = bool(health.get("broken_symlinks") or health.get("staging_files"))
        badge = f"💾 {total_gb:.2f} GB used · {free_gb:.1f} GB free"
        if warn:
            st.error(badge + " ⚠️")
            with st.expander("Library health warnings", expanded=False):
                if health.get("broken_symlinks"):
                    st.markdown("**Broken symlinks:**")
                    for b in health["broken_symlinks"]:
                        st.markdown(
                            f"- `{b['name']}` → `{b['target']}` (target unreachable inside container)"
                        )
                if health.get("staging_files"):
                    st.markdown(
                        f"**{health['staging_files']} interrupted upload(s)** under `.staging/` — safe to ignore unless persistent."
                    )
        else:
            st.caption(badge)
    except (requests.exceptions.RequestException, KeyError, ValueError):
        st.caption("💾 Library health unavailable")

    st.divider()

    # 2. MIDDLE: Session History (filtered by mode)
    st.header("Session History")
    if STATE_DIR.exists():
        all_sessions = sorted(
            [d.name for d in STATE_DIR.iterdir() if d.is_dir()], reverse=True
        )
        if st.session_state.mode == "Evolution":
            sessions = [s for s in all_sessions if is_evolution_session(s)]
        else:
            sessions = [s for s in all_sessions if not is_evolution_session(s)]

        if not sessions:
            st.caption("No past sessions found.")
        else:
            # border=False makes it look clean, while height=400 makes it scrollable!
            with st.container(height=400, border=False):
                for sid in sessions:
                    col1, col2 = st.columns([0.8, 0.2], vertical_alignment="center")

                    btn_type = (
                        "primary" if sid == st.session_state.session_id else "secondary"
                    )
                    if col1.button(
                        sid, key=f"hist_{sid}", type=btn_type, use_container_width=True
                    ):
                        st.session_state.session_id = sid
                        st.rerun()

                    # Trash can delete button (Ensure use_container_width is NOT set here)
                    if col2.button("🗑️", key=f"del_{sid}", help="Delete session"):
                        if st.session_state.skip_delete_confirm:
                            # Direct deletion
                            session_path = STATE_DIR / sid
                            if session_path.exists():
                                shutil.rmtree(session_path)
                            if st.session_state.session_id == sid:
                                st.session_state.session_id = None
                            st.rerun()
                        else:
                            # Pop-up confirmation
                            confirm_deletion(sid)
    else:
        st.caption("No past sessions found.")

    st.divider()

    # 3. BOTTOM: Human-in-the-Loop Dashboard
    st.header("Human-in-the-Loop")
    if st.session_state.session_id:
        try:
            hitl_resp = requests.get(
                f"{API_URL}/hitl/{st.session_state.session_id}/pending"
            )
            if hitl_resp.status_code == 200:
                pending = hitl_resp.json()
                if not pending:
                    st.info("No pending requests.")
                else:
                    is_awaiting_human = True  # Trigger Green Status
                    for req in pending:
                        # Older-format fallback: req might be just an ID string.
                        if isinstance(req, str):
                            req_id, kind, summary, payload = req, "unknown", req, {}
                        else:
                            req_id = req.get("id", "unknown")
                            kind = req.get("kind", "unknown")
                            summary = req.get("summary", req_id)
                            payload = req.get("payload", {}) or {}

                        st.warning(f"Pending: {summary}")

                        # Evolution-merge HITL: render diff + rationale + branch.
                        if kind == "evolution_merge":
                            branch = payload.get("branch", "?")
                            strict = payload.get("strict", False)
                            rationale = payload.get("rationale", "")
                            diff_preview = payload.get("diff_preview", "")

                            st.caption(
                                f"branch: `{branch}`"
                                + ("  •  ⚠ strict (smoke gated)" if strict else "")
                            )
                            if rationale:
                                with st.expander("Rationale"):
                                    st.markdown(rationale)
                            if diff_preview:
                                with st.expander("Diff", expanded=True):
                                    st.code(diff_preview, language="diff")

                        note = st.text_input("Optional Note", key=f"note_{req_id}")
                        c1, c2 = st.columns(2)
                        if c1.button("Approve", key=f"app_{req_id}", type="primary"):
                            requests.post(
                                f"{API_URL}/hitl/{st.session_state.session_id}/{req_id}/answer",
                                json={"decision": "approve", "note": note},
                            )
                            st.rerun()
                        if c2.button("Reject", key=f"rej_{req_id}"):
                            requests.post(
                                f"{API_URL}/hitl/{st.session_state.session_id}/{req_id}/answer",
                                json={"decision": "reject", "note": note},
                            )
                            st.rerun()
        except requests.exceptions.RequestException:
            st.error("Failed to connect to API for HITL status.")
    else:
        st.info("Select or start a session to view HITL requests.")

# --- MAIN UI: Header & Status Indicator ---
mode = st.session_state.mode
title_text = "Coscientist Chat" if mode == "Research" else "Coscientist — Evolution"

if st.session_state.session_id is None:
    st.title(title_text)

    if mode == "Research":
        st.info(
            "👋 Welcome! Enter your initial research question below to begin a new session."
        )
        if library_files:
            names = ", ".join(f["name"] for f in library_files[:6])
            extra = (
                "" if len(library_files) <= 6 else f" (+{len(library_files) - 6} more)"
            )
            st.caption(
                f"📎 {len(library_files)} file(s) in library available to the agent: {names}{extra}"
            )
        if task_input := st.chat_input("What would you like to research?"):
            with st.spinner("Starting session..."):
                try:
                    resp = requests.post(
                        f"{API_URL}/research/sessions", json={"task": task_input}
                    ).json()
                    st.session_state.session_id = resp["session_id"]
                    st.rerun()
                except Exception as e:
                    st.error(f"Error starting session: {e}")
    else:
        st.info(
            "🧬 Evolution mode. Enter a command for the meta-agent — e.g. "
            "*Add a critic subagent that reviews each generalist_researcher report.* "
            "The agent will work in a sandboxed worktree and ask you to approve any merge."
        )
        if cmd_input := st.chat_input("What should the meta-agent change?"):
            with st.spinner("Dispatching evolution agent..."):
                try:
                    resp = requests.post(
                        f"{API_URL}/evolution/commands", json={"command": cmd_input}
                    ).json()
                    st.session_state.session_id = resp["session_id"]
                    st.rerun()
                except Exception as e:
                    st.error(f"Error starting evolution session: {e}")

else:
    events_file = STATE_DIR / st.session_state.session_id / "events.jsonl"

    # Quick pre-read to check if the session is dead (Red status)
    is_dead = False
    if events_file.exists():
        try:
            with open(events_file, "r") as f:
                # Filter out blank/newline-only lines at the end of the file
                lines = [line.strip() for line in f if line.strip()]
                if lines:
                    last_event = json.loads(lines[-1])
                    if last_event.get("kind") in [
                        "session.ended",
                        "session.end",
                        "session.error",
                        "session.fatal",
                        "evolution.end",
                        "evolution.merged",
                        "evolution.rejected",
                        "evolution.auto_reject",
                        "evolution.crashed",
                    ]:
                        is_dead = True
        except Exception:
            pass

    # Determine Status Color and Text
    if is_dead:
        status_color = "#dc3545"  # Red
        status_text = "Session Ended"
    elif is_awaiting_human:
        status_color = "#28a745"  # Green
        status_text = "Awaiting Human Input"
    else:
        status_color = "#fd7e14"  # Orange
        status_text = "Processing..."

    # Render Header with Status Dot
    col1, col2 = st.columns([0.7, 0.3])
    with col1:
        st.title(title_text)
    with col2:
        st.markdown(
            f"""
            <div style="display: flex; align-items: center; justify-content: flex-end; height: 100%; padding-top: 30px;">
                <div style="width: 14px; height: 14px; border-radius: 50%; background-color: {status_color}; margin-right: 8px; box-shadow: 0 0 5px {status_color};"></div>
                <span style="color: #888; font-size: 15px; font-weight: 500;">{status_text}</span>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # --- MAIN UI: Event stream ---
    if events_file.exists():
        with open(events_file, "r") as f:
            all_lines = [line for line in f if line.strip()]
            last100_lines = all_lines[-100:]

            for i, line in enumerate(last100_lines):
                try:
                    event = json.loads(line)
                    actor = event.get("actor", "assistant")
                    # role = "user" if actor in ["human", "user"] else "assistant"
                    kind = event.get("kind", "")

                    # Consolidate text extraction
                    hastext = bool(
                        event.get("text", False)
                        or event.get("payload", {}).get("text", False)
                        or event.get("summary", False)
                        or (actor == "human" and event.get("command", False))
                        or (actor == "human" and event.get("task", False))
                    )

                    if not hastext and i < len(last100_lines) - 10:
                        continue

                    if kind == "research.requested":
                        with st.chat_message("user"):
                            st.markdown(f"**Research Task:**\n {event.get('task')}")

                    elif kind == "evolution.requested":
                        with st.chat_message("user"):
                            st.markdown(
                                f"**Evolution Command:**\n {event.get('command')}"
                            )

                    elif kind == "evolution.proposal":
                        with st.chat_message("assistant"):
                            ref = event.get("ref", "?")
                            strict_flag = " ⚠ strict" if event.get("strict") else ""
                            st.markdown(
                                f"**Merge proposal** archived as `{ref}`{strict_flag}"
                            )

                    elif kind == "evolution.merged":
                        with st.chat_message("assistant"):
                            st.success(
                                f"✅ Merged: `{event.get('ref', '?')}`. Restart sessions to pick up changes."
                            )

                    elif kind == "evolution.note":
                        with st.chat_message("assistant"):
                            st.success(
                                f"📒 Noted: `{event.get('text', 'Empty Notes')}` "
                            )

                    elif kind in ("evolution.rejected", "evolution.auto_reject"):
                        with st.chat_message("assistant"):
                            st.error(
                                f"❌ {kind.split('.')[-1].replace('_', ' ').title()}: `{event.get('ref', '?')}`"
                            )

                    elif kind == "evolution.crashed":
                        with st.chat_message("assistant"):
                            st.error(
                                f"💥 Evolution agent crashed: {event.get('error', 'unknown')}"
                            )

                    elif actor == "human" and event.get("text"):  # human feedback
                        with st.chat_message("user"):
                            st.markdown(event.get("text"))

                    elif kind == "bus.send":
                        target = event.get("target", "unknown")
                        msg_kind = event.get("msg_kind", "message")

                        if event.get("payload", {}).get("text", None):
                            with st.chat_message("assistant"):
                                st.markdown(
                                    f"**To {target}:**\n{event.get("payload").get("text")}"
                                )
                        else:
                            with st.expander(
                                f"✉️ {msg_kind.capitalize()} sent to {target}"
                            ):
                                st.json(event)

                    elif kind == "session.turn_result":
                        summary_text = event.get("summary", None) or "Complete"
                        with st.expander(f"🔄 Turn Result: {summary_text}"):
                            st.json(event)

                    else:
                        with st.expander(f"⚙️ System Event: {kind}"):
                            st.json(event)

                except json.JSONDecodeError:
                    pass
    else:
        st.info("Waiting for agent to initialize and log events...")

    # --- MAIN UI: Input ---
    if mode == "Research":
        # Mid-session directives go to the supervisor.
        if prompt := st.chat_input("Send a directive to the supervisor..."):
            requests.post(
                f"{API_URL}/research/sessions/{st.session_state.session_id}/messages",
                json={"text": prompt},
            )
            st.rerun()
    else:
        # Evolution agent is short-lived per command — there's no live process to direct.
        # Offer a "queue another command in this session" form instead.
        with st.form("evo_followup", clear_on_submit=True):
            followup = st.text_area(
                "Queue another command on this session",
                placeholder="e.g. Now add a unit test for the critic role.",
                height=80,
            )
            submitted = st.form_submit_button("Dispatch", type="primary")
            if submitted and followup.strip():
                try:
                    requests.post(
                        f"{API_URL}/evolution/commands",
                        json={
                            "command": followup,
                            "session_id": st.session_state.session_id,
                        },
                    )
                    st.rerun()
                except Exception as e:
                    st.error(f"Error dispatching command: {e}")

    # Auto-refresh loop to pull new events
    time.sleep(2)
    st.rerun()
