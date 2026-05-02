import streamlit as st
import requests
import json
import time
from pathlib import Path

# Docker internal networking routes this to the API container
API_URL = "http://coscientist-api:8765"

st.set_page_config(page_title="Coscientist UI", layout="wide")

# Initialize session state
if "session_id" not in st.session_state:
    st.session_state.session_id = None

# --- SIDEBAR: Controls & HITL ---
with st.sidebar:
    st.header("Start a Session")
    task_input = st.text_area("Research Task")
    if st.button("Start Research"):
        resp = requests.post(f"{API_URL}/research/sessions", json={"task": task_input}).json()
        st.session_state.session_id = resp["session_id"]
        st.rerun()
        
    st.divider()
    
    if st.session_state.session_id:
        st.success(f"Active Session:\n{st.session_state.session_id}")
        
        # Phase 3: Human-in-the-Loop (HITL) Dashboard
        st.header("Human-in-the-Loop")
        try:
            hitl_resp = requests.get(f"{API_URL}/hitl/{st.session_state.session_id}/pending")
            if hitl_resp.status_code == 200:
                pending = hitl_resp.json()
                if not pending:
                    st.info("No pending requests.")
                else:
                    for req in pending:
                        # Handle potential response formats (list of strings or dicts)
                        req_id = req if isinstance(req, str) else req.get("id", "unknown")
                        st.warning(f"Pending Approval: {req_id}")
                        note = st.text_input("Optional Note", key=f"note_{req_id}")
                        
                        col1, col2 = st.columns(2)
                        if col1.button("Approve", key=f"app_{req_id}", type="primary"):
                            requests.post(f"{API_URL}/hitl/{st.session_state.session_id}/{req_id}/answer",
                                          json={"decision": "approve", "note": note})
                            st.rerun()
                        if col2.button("Reject", key=f"rej_{req_id}"):
                            requests.post(f"{API_URL}/hitl/{st.session_state.session_id}/{req_id}/answer",
                                          json={"decision": "reject", "note": note})
                            st.rerun()
        except requests.exceptions.RequestException:
            st.error("Failed to connect to API for HITL status.")

# --- MAIN UI: Chat & Event Stream ---
st.title("Coscientist Chat")

if st.session_state.session_id:
    # Read the event log directly from the shared filesystem
    state_dir = Path("/app/state/sessions") / st.session_state.session_id
    events_file = state_dir / "events.jsonl"
    
    if events_file.exists():
        with open(events_file, "r") as f:
            for line in f:
                try:
                    event = json.loads(line)
                    # Differentiate human vs agent visually
                    role = "user" if event.get("actor") == "human" else "assistant"
                    with st.chat_message(role):
                        st.json(event) # Renders the JSON nicely in the UI
                except json.JSONDecodeError:
                    pass
    else:
        st.info("Waiting for agent to initialize and log events...")

    # Human Directive Input
    if prompt := st.chat_input("Send a directive to the supervisor..."):
        requests.post(
            f"{API_URL}/research/sessions/{st.session_state.session_id}/messages",
            json={"text": prompt}
        )
        st.rerun()
        
    # Auto-refresh loop to pull new events
    time.sleep(2)
    st.rerun()
else:
    st.write("👈 Start a research task in the sidebar to begin.")