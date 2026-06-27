import json
import os

import requests
import streamlit as st
from streamlit_autorefresh import st_autorefresh


API_URL = os.environ.get("COSCIENTIST_API_URL", "http://127.0.0.1:8765")


st.set_page_config(page_title="Coscientist Chat", layout="wide")

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


def _build_git_dot(commits, head):
    """Render the full branch graph as Graphviz DOT.

    Newest commits sit on top (rankdir=TB, edges child->parent). main history is
    green, in-flight evo/* branch tips orange, HEAD drawn with a bold border."""

    def esc(s):
        return s.replace("\\", "\\\\").replace('"', '\\"')

    lines = [
        "digraph G {",
        "rankdir=TB;",
        'node [shape=box style="rounded,filled" fontname="monospace" fontsize=9];',
        'edge [arrowsize=0.6 color="#888888"];',
    ]
    known = {c["sha"] for c in commits}
    for c in commits:
        sha = c["sha"]
        refs = c.get("refs", []) or []
        subj = c.get("subject", "") or ""
        is_main = any(r == "main" or r.endswith("/main") for r in refs)
        is_evo = any(r.startswith("evo/") for r in refs)
        is_head = bool(head) and sha == head
        if is_evo:
            fill, border = "#ffe8cc", "#fd7e14"
        elif is_main:
            fill, border = "#d3f9d8", "#28a745"
        else:
            fill, border = "#f1f3f5", "#adb5bd"
        subj_short = subj if len(subj) <= 38 else subj[:37] + "…"
        label = esc(f"{sha[:7]}  {subj_short}")
        if refs:
            label += "\\n" + esc("[" + ", ".join(refs) + "]")
        penwidth = "2.5" if is_head else "1"
        lines.append(
            f'"{sha}" [label="{label}" fillcolor="{fill}" '
            f'color="{border}" penwidth={penwidth}];'
        )
    for c in commits:
        for p in c.get("parents", []) or []:
            if p in known:
                lines.append(f'"{c["sha"]}" -> "{p}";')
    lines.append("}")
    return "\n".join(lines)


def _headers() -> dict[str, str]:
    return {"Authorization": f"Bearer {st.session_state.api_key}"}


def api_request(method: str, path: str, **kwargs) -> requests.Response:
    headers = kwargs.pop("headers", {})
    headers.update(_headers())
    kwargs.setdefault("timeout", 10)
    resp = requests.request(method, f"{API_URL}{path}", headers=headers, **kwargs)
    if resp.status_code == 401:
        for key in ("api_key", "user", "session_id"):
            st.session_state.pop(key, None)
        st.warning("Session expired or API key was rejected.")
        st.rerun()
    return resp


@st.cache_data(show_spinner=False)
def _fetch_session_file(api_key: str, sid: str, path: str, mtime: float) -> bytes:
    """Fetch one generated file's bytes. Cached on (sid, path, mtime) so the
    2s autorefresh doesn't re-download unchanged files."""
    r = requests.get(
        f"{API_URL}/sessions/{sid}/files/download",
        params={"path": path},
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=60,
    )
    r.raise_for_status()
    return r.content


def check_login() -> bool:
    if "api_key" in st.session_state and "user" in st.session_state:
        return True

    st.markdown("<div style='padding-top: 50px;'></div>", unsafe_allow_html=True)
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.title("Coscientist")
        api_key = st.text_input("API key", type="password")
        if st.button("Log in", type="primary", use_container_width=True):
            try:
                resp = requests.get(
                    f"{API_URL}/auth/me",
                    headers={"Authorization": f"Bearer {api_key.strip()}"},
                    timeout=30,
                )
            except requests.exceptions.RequestException as e:
                st.error(f"Could not reach API: {e}")
                return False
            if resp.status_code == 200:
                st.session_state.api_key = api_key.strip()
                st.session_state.user = resp.json()
                st.session_state.session_id = None
                st.rerun()
            else:
                st.error("Invalid API key.")
    return False


def clear_login_state() -> None:
    for key in (
        "api_key",
        "user",
        "session_id",
        "pending_delete_sid",
        "skip_delete_confirm",
    ):
        st.session_state.pop(key, None)


if check_login():
    if "session_id" not in st.session_state:
        st.session_state.session_id = None
    if "skip_delete_confirm" not in st.session_state:
        st.session_state.skip_delete_confirm = False
    if "pending_delete_sid" not in st.session_state:
        st.session_state.pending_delete_sid = None

    user = st.session_state.user

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
            resp = api_request("DELETE", f"/sessions/{session_id}", timeout=30)
            if resp.status_code not in (200, 404):
                st.error(f"Delete failed: {resp.text}")
                return
            if st.session_state.session_id == session_id:
                st.session_state.session_id = None
            st.session_state.pending_delete_sid = None
            st.rerun()
        if col2.button("Cancel", use_container_width=True):
            st.session_state.pending_delete_sid = None
            st.rerun()

    is_awaiting_human = False
    events: list[dict] = []
    library_files: list[dict] = []

    with st.sidebar:
        st.caption(f"Signed in as {user['display_name']}")
        if st.button("Log out", use_container_width=True):
            clear_login_state()
            st.rerun()

        with st.expander("Agent API key", expanded=False):
            try:
                key_status_resp = api_request("GET", "/auth/agent-key", timeout=5)
                key_status = key_status_resp.json() if key_status_resp.status_code == 200 else {}
            except requests.exceptions.RequestException:
                key_status = {}

            has_custom_key = bool(key_status.get("has_custom_key"))
            default_available = bool(key_status.get("default_available"))
            if has_custom_key:
                st.caption("Using your saved API key for new agent sessions.")
            elif default_available:
                st.caption("Using the server default API key for new agent sessions.")
            else:
                st.warning("No default API key is configured. Save a key before starting sessions.")

            new_agent_key = st.text_input(
                "Override key",
                type="password",
                key="agent_api_key_input",
                label_visibility="collapsed",
                placeholder="Paste a user-specific Anthropic API key",
            )
            c_key_save, c_key_default = st.columns(2)
            if c_key_save.button("Save", use_container_width=True):
                if not new_agent_key.strip():
                    st.error("Paste a key before saving.")
                else:
                    resp = api_request(
                        "PUT",
                        "/auth/agent-key",
                        json={"api_key": new_agent_key.strip()},
                        timeout=10,
                    )
                    if resp.status_code == 200:
                        st.success("Saved for future sessions.")
                        st.rerun()
                    else:
                        st.error(f"Save failed: {resp.text}")
            if c_key_default.button("Use default", use_container_width=True):
                resp = api_request("DELETE", "/auth/agent-key", timeout=10)
                if resp.status_code == 200:
                    st.success("Using server default for future sessions.")
                    st.rerun()
                else:
                    st.error(f"Update failed: {resp.text}")

        if st.button("New Session", type="primary", use_container_width=True):
            st.session_state.session_id = None
            st.rerun()

        st.divider()

        st.header("Library")
        try:
            files_resp = api_request("GET", "/library/files", timeout=5)
            library_files = files_resp.json() if files_resp.status_code == 200 else []
        except requests.exceptions.RequestException:
            library_files = []

        uploaded = st.file_uploader(
            "Upload",
            accept_multiple_files=True,
            label_visibility="collapsed",
            key=f"lib_uploader_{len(library_files)}",
        )
        if uploaded:
            for f in uploaded:
                try:
                    api_request(
                        "POST",
                        "/library/files",
                        files={"file": (f.name, f.getvalue())},
                        timeout=600,
                    )
                except requests.exceptions.RequestException as e:
                    st.error(f"Upload failed for {f.name}: {e}")
            st.rerun()

        st.caption(
            "Large files can be placed on the host under this user's private state/users/<user>/root/state/library/ path."
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
                        f"**{lf['name']}**  \n<span style='color:#888;font-size:11px'>{size_str}</span>",
                        unsafe_allow_html=True,
                    )
                    if col_r.button("Del", key=f"lib_del_{lf['name']}", help=f"Delete {lf['name']}"):
                        try:
                            api_request("DELETE", f"/library/files/{lf['name']}", timeout=10)
                        except requests.exceptions.RequestException:
                            pass
                        st.rerun()
        else:
            st.caption("No files in library yet.")

        try:
            health = api_request("GET", "/library/health", timeout=5).json()
            total_gb = health["total_bytes"] / (1024**3)
            free_gb = health["disk_free_bytes"] / (1024**3)
            warn = bool(health.get("broken_symlinks") or health.get("staging_files"))
            badge = f"{total_gb:.2f} GB used · {free_gb:.1f} GB free"
            if warn:
                st.error(badge)
                with st.expander("Library health warnings", expanded=False):
                    if health.get("broken_symlinks"):
                        st.markdown("**Broken symlinks:**")
                        for b in health["broken_symlinks"]:
                            st.markdown(
                                f"- `{b['name']}` -> `{b['target']}` (target unreachable inside container)"
                            )
                    if health.get("staging_files"):
                        st.markdown(
                            f"**{health['staging_files']} interrupted upload(s)** under `.staging/`."
                        )
            else:
                st.caption(badge)
        except (requests.exceptions.RequestException, KeyError, ValueError):
            st.caption("Library health unavailable")

        st.divider()

        st.header("Session History")
        try:
            sessions_resp = api_request("GET", "/sessions", timeout=10)
            sessions = sessions_resp.json() if sessions_resp.status_code == 200 else []
        except requests.exceptions.RequestException:
            sessions = []

        if not sessions:
            st.caption("No past sessions found.")
        else:
            with st.container(height=400, border=False):
                for sess in sessions:
                    sid = sess["session_id"]
                    col1, col2 = st.columns([0.8, 0.2], vertical_alignment="center")
                    btn_type = "primary" if sid == st.session_state.session_id else "secondary"
                    label = sid + (" *" if sess.get("running") else "")
                    if col1.button(label, key=f"hist_{sid}", type=btn_type, use_container_width=True):
                        st.session_state.session_id = sid
                        st.rerun()
                    if col2.button("Del", key=f"del_{sid}", help="Delete session"):
                        if st.session_state.skip_delete_confirm:
                            resp = api_request("DELETE", f"/sessions/{sid}", timeout=30)
                            if resp.status_code == 200 and st.session_state.session_id == sid:
                                st.session_state.session_id = None
                            st.rerun()
                        else:
                            st.session_state.pending_delete_sid = sid
                            st.rerun()

        st.divider()

        # Evolution git graph — full branch graph across all refs. The UI
        # container has no .git mount, so this comes from the API container.
        with st.expander("🌳 Evolution Git Graph", expanded=False):
            try:
                git_resp = api_request("GET", "/git/history?limit=5", timeout=5)
                git_history = git_resp.json() if git_resp.status_code == 200 else None
            except requests.exceptions.RequestException:
                git_history = None

            if git_history and git_history.get("commits"):
                st.graphviz_chart(
                    _build_git_dot(git_history["commits"], git_history.get("head")),
                    use_container_width=True,
                )
                st.caption("🟢 main · 🟠 in-flight evo/* · bold border = HEAD")
            elif git_history is not None:
                st.caption("No commits yet.")
            else:
                st.caption("Git graph unavailable (API unreachable).")

        st.divider()

        st.header("Human-in-the-Loop")
        if st.session_state.session_id:
            try:
                hitl_resp = api_request(
                    "GET", f"/hitl/{st.session_state.session_id}/pending", timeout=10
                )
                if hitl_resp.status_code == 200:
                    pending = hitl_resp.json()
                    if not pending:
                        st.info("No pending requests.")
                    else:
                        is_awaiting_human = True
                        for req in pending:
                            req_id = req.get("id", "unknown")
                            kind = req.get("kind", "unknown")
                            summary = req.get("summary", req_id)
                            payload = req.get("payload", {}) or {}
                            st.warning(f"Pending: {summary}")

                            if kind == "evolution_merge":
                                branch = payload.get("branch", "?")
                                strict = payload.get("strict", False)
                                rationale = payload.get("rationale", "")
                                diff_preview = payload.get("diff_preview", "")
                                st.caption(
                                    f"branch: `{branch}`"
                                    + (" · strict smoke gated" if strict else "")
                                )
                                if rationale:
                                    with st.expander("Rationale"):
                                        st.markdown(rationale)
                                if diff_preview:
                                    with st.expander("Diff", expanded=True):
                                        st.code(diff_preview, language="diff")

                            elif kind == "skill_proposal":
                                desc = payload.get("description", "")
                                overwrite = payload.get("overwrite", False)
                                if desc:
                                    st.caption(
                                        desc
                                        + (" · ⚠ overwrites an existing skill" if overwrite else "")
                                    )
                                skill_md = payload.get("skill_md", "")
                                if skill_md:
                                    with st.expander("SKILL.md", expanded=True):
                                        st.code(skill_md, language="markdown")

                            note = st.text_input("Optional Note", key=f"note_{req_id}")
                            c1, c2 = st.columns(2)
                            if c1.button("Approve", key=f"app_{req_id}", type="primary"):
                                api_request(
                                    "POST",
                                    f"/hitl/{st.session_state.session_id}/{req_id}/answer",
                                    json={"decision": "approve", "note": note},
                                    timeout=10,
                                )
                                st.rerun()
                            if c2.button("Reject", key=f"rej_{req_id}"):
                                api_request(
                                    "POST",
                                    f"/hitl/{st.session_state.session_id}/{req_id}/answer",
                                    json={"decision": "reject", "note": note},
                                    timeout=10,
                                )
                                st.rerun()
                else:
                    st.error(f"HITL API returned {hitl_resp.status_code}")
            except requests.exceptions.RequestException:
                st.error("Failed to connect to API for HITL status.")
        else:
            st.info("Select or start a session to view HITL requests.")

    if st.session_state.pending_delete_sid:
        confirm_deletion(st.session_state.pending_delete_sid)

    title_text = "Coscientist Chat"

    if st.session_state.session_id is None:
        st.title(title_text)
        st.info("Welcome. Enter your initial research question below to begin a new session.")
        if library_files:
            names = ", ".join(f["name"] for f in library_files[:6])
            extra = "" if len(library_files) <= 6 else f" (+{len(library_files) - 6} more)"
            st.caption(
                f"{len(library_files)} file(s) in library available to the agent: {names}{extra}"
            )
        if task_input := st.chat_input("What would you like to research?"):
            with st.spinner("Starting session..."):
                try:
                    resp = api_request(
                        "POST", "/research/sessions", json={"task": task_input}, timeout=30
                    )
                    resp.raise_for_status()
                    st.session_state.session_id = resp.json()["session_id"]
                    st.rerun()
                except Exception as e:
                    st.error(f"Error starting session: {e}")

    else:
        try:
            events_resp = api_request(
                "GET", f"/sessions/{st.session_state.session_id}/events", timeout=10
            )
            events = events_resp.json() if events_resp.status_code == 200 else []
        except requests.exceptions.RequestException:
            events = []

        # Classify session state from the last event.
        #   is_idle — a turn finished; the session is resumable (send a follow-up).
        #   is_dead — hard stop/crash with no resumable state.
        last_kind = events[-1].get("kind") if events else None
        is_idle = last_kind == "session.idle"
        is_dead = last_kind in {
            "session.ended",
            "session.end",
            "session.error",
            "session.fatal",
            "session.crashed",
        }

        if is_dead:
            status_color = "#dc3545"
            status_text = "Session Ended"
        elif is_idle:
            status_color = "#0d6efd"
            status_text = "Idle — send a message"
        elif is_awaiting_human:
            status_color = "#28a745"
            status_text = "Awaiting Human Input"
        else:
            status_color = "#fd7e14"
            status_text = "Processing..."

        col1, col2, col3 = st.columns([0.6, 0.25, 0.15])
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
        with col3:
            if not is_dead and not is_idle and not is_awaiting_human:
                st.markdown("<div style='padding-top: 30px;'></div>", unsafe_allow_html=True)
                if st.button("Stop", type="secondary", use_container_width=True):
                    try:
                        api_request(
                            "POST", f"/sessions/{st.session_state.session_id}/stop", timeout=5
                        )
                    except requests.exceptions.RequestException:
                        st.error("Failed to stop session.")
                    st.rerun()

        # Generated files — the agent's outputs (results/ and scratch/), now
        # downloadable directly from the UI instead of only from the backend.
        try:
            files_resp = api_request(
                "GET", f"/sessions/{st.session_state.session_id}/files", timeout=10
            )
            gen_files = files_resp.json() if files_resp.status_code == 200 else []
        except requests.exceptions.RequestException:
            gen_files = []

        with st.expander(f"📁 Generated files ({len(gen_files)})", expanded=bool(gen_files)):
            if not gen_files:
                st.caption("No files yet — the agent writes outputs to results/ and scratch/.")
            else:
                for gf in gen_files:
                    c1, c2, c3 = st.columns([0.62, 0.18, 0.20], vertical_alignment="center")
                    size_kb = gf["size"] / 1024
                    size_str = (
                        f"{size_kb / 1024:.1f} MB"
                        if size_kb >= 1024
                        else f"{size_kb:.1f} KB" if size_kb >= 1 else f"{gf['size']} B"
                    )
                    c1.markdown(f"`{gf['path']}`")
                    c2.caption(size_str)
                    if gf["size"] <= 25 * 1024 * 1024:
                        try:
                            data = _fetch_session_file(
                                st.session_state.api_key,
                                st.session_state.session_id,
                                gf["path"],
                                gf["mtime"],
                            )
                            c3.download_button(
                                "Download",
                                data=data,
                                file_name=gf["path"].split("/")[-1],
                                key=f"dl_{gf['path']}",
                                use_container_width=True,
                            )
                        except Exception:
                            c3.caption("unavailable")
                    else:
                        c3.caption("too large")

        if events:
            last100 = events[-100:]
            for i, event in enumerate(last100):
                actor = event.get("actor", "assistant")
                kind = event.get("kind", "")
                payload = event.get("payload", {}) or {}
                hastext = bool(
                    event.get("text")
                    or payload.get("text")
                    or event.get("summary")
                    or (actor == "human" and event.get("command"))
                    or (actor == "human" and event.get("task"))
                )
                if (not hastext) and i < len(last100) - 5:
                    continue
                if kind in {
                    "session.turn_result",
                    "checkpoint.resolved",
                    "session.end",
                    "hitl.pending",
                    "hitl.answer",
                } and i < len(last100) - 5:
                    continue

                if kind == "research.requested":
                    with st.chat_message("user"):
                        st.markdown(f"**Research Task:**\n {event.get('task')}")
                elif kind == "evolution.requested":
                    with st.chat_message("user"):
                        st.markdown(f"**Evolution Command:**\n {event.get('command')}")
                elif kind == "evolution.proposal":
                    with st.chat_message("assistant"):
                        ref = event.get("ref", "?")
                        strict_flag = " strict" if event.get("strict") else ""
                        st.markdown(f"**Merge proposal** archived as `{ref}`{strict_flag}")
                elif kind == "evolution.merged":
                    with st.chat_message("assistant"):
                        st.success(
                            f"Merged: `{event.get('ref', '?')}`. Restart sessions to pick up changes."
                        )
                elif kind == "evolution.note":
                    with st.chat_message("assistant"):
                        st.success(f"Noted: `{event.get('text', 'Empty Notes')}`")
                elif kind in ("evolution.rejected", "evolution.auto_reject"):
                    with st.chat_message("assistant"):
                        st.error(
                            f"{kind.split('.')[-1].replace('_', ' ').title()}: `{event.get('ref', '?')}`"
                        )
                elif kind == "evolution.crashed":
                    with st.chat_message("assistant"):
                        st.error(f"Evolution agent crashed: {event.get('error', 'unknown')}")
                elif kind == "checkpoint.triggered":
                    with st.chat_message("assistant"):
                        n_event = event.get("event_count", "?")
                        ckpt_summary = event.get("summary", "")
                        st.markdown(f"**Checkpoint** ({n_event} events)\n\n{ckpt_summary}")
                elif kind == "skill.proposed":
                    with st.chat_message("assistant"):
                        st.markdown(f"💡 Proposed skill: `{event.get('name', '?')}` — awaiting your approval.")
                elif kind == "skill.approved":
                    with st.chat_message("assistant"):
                        st.success(f"🧩 Saved skill: `{event.get('name', '?')}` — available to future sessions.")
                elif kind == "skill.rejected":
                    with st.chat_message("assistant"):
                        st.info(f"Skill `{event.get('name', '?')}` was not saved.")
                elif actor == "human" and event.get("text"):
                    with st.chat_message("user"):
                        st.markdown(event.get("text"))
                elif kind == "bus.send":
                    target = event.get("target", "unknown")
                    msg_kind = event.get("msg_kind", "message")
                    if payload.get("text"):
                        with st.chat_message("assistant"):
                            st.markdown(f"**To {target}:**\n{payload.get('text')}")
                    else:
                        with st.expander(f"{msg_kind.capitalize()} sent to {target}"):
                            st.json(event)
                elif kind == "session.turn_result":
                    summary_text = event.get("summary") or "Complete"
                    with st.expander(f"Turn Result: {summary_text}"):
                        st.json(event)
                else:
                    with st.expander(f"System Event {event.get('ts', '')}: {kind}"):
                        st.json(event)
        else:
            st.info("Waiting for agent to initialize and log events...")

        # The chat input is live in two states:
        #   idle  — a turn finished; a message resumes the conversation.
        #   awaiting-human — a HITL prompt is pending; a message is sent to the
        #     agent mid-turn (you can talk to it before deciding Approve/Deny in
        #     the sidebar). For an open-ended question your message is the reply.
        # While a turn is actively running (neither state), the box is disabled.
        if is_idle or is_awaiting_human:
            placeholder = (
                "Reply to continue the conversation…"
                if is_idle
                else "Message the agent… (Approve/Deny in the sidebar to finalize)"
            )
            if msg := st.chat_input(placeholder):
                if is_idle:
                    endpoint = f"/research/sessions/{st.session_state.session_id}/messages"
                    spinner = "Resuming…"
                else:
                    endpoint = f"/research/sessions/{st.session_state.session_id}/interject"
                    spinner = "Sending…"
                with st.spinner(spinner):
                    try:
                        resp = api_request("POST", endpoint, json={"text": msg}, timeout=10)
                        if resp.status_code == 409:
                            st.warning(
                                "Session is still working — Stop the current turn or wait."
                            )
                        elif resp.status_code >= 400:
                            st.error(f"Could not send message: {resp.text}")
                        else:
                            st.rerun()
                    except requests.exceptions.RequestException as e:
                        st.error(f"Could not send message: {e}")
        else:
            placeholder = (
                "Session ended."
                if is_dead
                else "Working… use the HITL panel in the sidebar to interact."
            )
            st.chat_input(placeholder, disabled=True)

        # Auto-refresh to pull new events. Skip only when the session ended, is
        # idle (nothing changes until you send), or the delete dialog is open.
        # During an awaiting-human HITL we DO keep refreshing so the agent's
        # replies to your chat messages appear.
        if (
            not is_dead
            and not is_idle
            and not st.session_state.pending_delete_sid
        ):
            st_autorefresh(interval=2000, key=f"refresh_{st.session_state.session_id}")
