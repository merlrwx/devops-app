import json
import math
import os
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import streamlit as st

BACKEND_URL = os.environ.get("BACKEND_URL", "http://127.0.0.1:8000").rstrip("/")
KINDS = ("focus", "short_break", "long_break")
LABELS = {"focus": "Focus", "short_break": "Short break", "long_break": "Long break"}


class BackendError(Exception):
    pass


def api_request(path: str, method: str = "GET", payload: dict | None = None) -> dict | list:
    body = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if body is not None else {}
    request = Request(f"{BACKEND_URL}{path}", data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=3) as response:
            return json.load(response)
    except HTTPError as error:
        try:
            detail = json.loads(error.read()).get("detail")
        except (AttributeError, json.JSONDecodeError):
            detail = None
        raise BackendError(detail or f"Backend returned HTTP {error.code}") from error
    except (URLError, TimeoutError) as error:
        reason = getattr(error, "reason", error)
        raise BackendError(f"Cannot reach the backend at {BACKEND_URL}: {reason}") from error


def remaining_seconds(timer: dict) -> int:
    if timer["status"] == "running" and timer["ends_at"]:
        ends_at = datetime.fromisoformat(timer["ends_at"])
        return max(0, math.ceil((ends_at - datetime.now(timezone.utc)).total_seconds()))
    return timer["remaining_seconds"]


def format_time(seconds: int) -> str:
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes:02}:{seconds:02}"


def perform_action(path: str, payload: dict | None = None) -> None:
    try:
        api_request(path, method="POST", payload=payload)
    except BackendError as error:
        st.error(str(error))
    else:
        st.rerun()


st.set_page_config(page_title="Pomodoro", page_icon="🍅", layout="centered")
st.title("Pomodoro")
st.caption("Make time for what matters. Work in one focused session at a time.")

try:
    timer = api_request("/api/timer")
    sessions = api_request("/api/sessions?limit=8")
except BackendError as error:
    st.error(str(error))
    st.stop()

if timer["status"] != "idle":
    st.session_state["selected_kind"] = timer["kind"]
elif "selected_kind" not in st.session_state:
    st.session_state["selected_kind"] = "focus"

selected_kind = st.radio(
    "Session type",
    options=KINDS,
    format_func=LABELS.__getitem__,
    horizontal=True,
    disabled=timer["status"] != "idle",
    key="selected_kind",
)

run_every = "1s" if timer["status"] == "running" else None


@st.fragment(run_every=run_every)
def show_timer() -> None:
    try:
        current = api_request("/api/timer")
    except BackendError as error:
        st.error(str(error))
        return

    if timer["status"] == "running" and current["status"] == "idle":
        st.rerun()

    remaining = remaining_seconds(current)
    st.metric("Time remaining", format_time(remaining) if current["status"] != "idle" else "--:--")
    if current["status"] != "idle":
        st.caption(f"{LABELS[current['kind']]} · {current['status'].capitalize()}")
        if current["duration_seconds"]:
            elapsed = 1 - remaining / current["duration_seconds"]
            st.progress(min(1.0, max(0.0, elapsed)))
    else:
        st.caption("Ready when you are")


show_timer()

if timer["status"] == "idle":
    if st.button(f"Start {LABELS[selected_kind]}", type="primary", use_container_width=True):
        perform_action("/api/timer/start", {"kind": selected_kind})
else:
    action, stop = st.columns(2)
    if timer["status"] == "running":
        if action.button("Pause", type="primary", use_container_width=True):
            perform_action("/api/timer/pause")
    elif action.button("Resume", type="primary", use_container_width=True):
        perform_action("/api/timer/resume")
    if stop.button("Stop", use_container_width=True):
        perform_action("/api/timer/stop")

st.divider()
st.subheader("Recent completed sessions")
if not sessions:
    st.info("Completed timers will appear here.")
else:
    for session in sessions:
        details, duration = st.columns((3, 1))
        completed_at = datetime.fromisoformat(session["completed_at"]).astimezone()
        details.write(f"**{LABELS[session['kind']]}**")
        details.caption(completed_at.strftime("%b %d · %H:%M"))
        seconds = session["duration_seconds"]
        duration.write(f"{seconds // 60} min" if seconds >= 60 else f"{seconds}s")
