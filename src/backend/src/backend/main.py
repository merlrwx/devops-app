from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timedelta, timezone
import math
import os
from pathlib import Path
import sqlite3
from typing import AsyncIterator, Literal

import uvicorn
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

TimerKind = Literal["focus", "short_break", "long_break"]
TimerStatus = Literal["idle", "running", "paused"]
MAX_DURATION_SECONDS = 3 * 60 * 60
DATABASE_PATH = Path(
    os.environ.get("DATABASE_PATH", "data/pomodoro.sqlite3")
).expanduser()


def configured_duration(name: str, default: int) -> int:
    value = int(os.environ.get(name, str(default)))
    if not 1 <= value <= MAX_DURATION_SECONDS:
        raise ValueError(f"{name} must be between 1 and {MAX_DURATION_SECONDS}")
    return value


DEFAULT_DURATIONS = {
    "focus": configured_duration("FOCUS_SECONDS", 25 * 60),
    "short_break": configured_duration("SHORT_BREAK_SECONDS", 5 * 60),
    "long_break": configured_duration("LONG_BREAK_SECONDS", 15 * 60),
}


class TimerStart(BaseModel):
    kind: TimerKind = "focus"
    duration_seconds: int | None = Field(default=None, ge=1, le=MAX_DURATION_SECONDS)


class TimerResponse(BaseModel):
    status: TimerStatus
    kind: TimerKind | None
    started_at: datetime | None
    ends_at: datetime | None
    remaining_seconds: int
    duration_seconds: int | None


class SessionResponse(BaseModel):
    id: int
    kind: TimerKind
    started_at: datetime
    completed_at: datetime
    duration_seconds: int


@contextmanager
def database(write: bool = False):
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATABASE_PATH, timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        if write:
            connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def idle_timer() -> TimerResponse:
    return TimerResponse(
        status="idle",
        kind=None,
        started_at=None,
        ends_at=None,
        remaining_seconds=0,
        duration_seconds=None,
    )


def set_idle(connection: sqlite3.Connection) -> None:
    connection.execute(
        """
        UPDATE timer_state
        SET status = 'idle', kind = NULL, started_at = NULL, ends_at = NULL,
            remaining_seconds = 0, duration_seconds = NULL
        WHERE id = 1
        """
    )


def current_timer(connection: sqlite3.Connection) -> TimerResponse:
    row = connection.execute("SELECT * FROM timer_state WHERE id = 1").fetchone()
    if row["status"] == "running":
        end = datetime.fromisoformat(row["ends_at"])
        now = datetime.now(timezone.utc)
        remaining = max(0, math.ceil((end - now).total_seconds()))
        if remaining == 0:
            connection.execute(
                """
                INSERT INTO sessions (kind, started_at, completed_at, duration_seconds)
                VALUES (?, ?, ?, ?)
                """,
                (row["kind"], row["started_at"], row["ends_at"], row["duration_seconds"]),
            )
            set_idle(connection)
            return idle_timer()
        return TimerResponse(
            status="running",
            kind=row["kind"],
            started_at=row["started_at"],
            ends_at=row["ends_at"],
            remaining_seconds=remaining,
            duration_seconds=row["duration_seconds"],
        )
    if row["status"] == "idle":
        return idle_timer()
    return TimerResponse(
        status="paused",
        kind=row["kind"],
        started_at=row["started_at"],
        ends_at=None,
        remaining_seconds=row["remaining_seconds"],
        duration_seconds=row["duration_seconds"],
    )


def initialize_database() -> None:
    with database(write=True) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS timer_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                status TEXT NOT NULL CHECK (status IN ('idle', 'running', 'paused')),
                kind TEXT,
                started_at TEXT,
                ends_at TEXT,
                remaining_seconds INTEGER NOT NULL DEFAULT 0 CHECK (remaining_seconds >= 0),
                duration_seconds INTEGER
            );
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY,
                kind TEXT NOT NULL,
                started_at TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                duration_seconds INTEGER NOT NULL CHECK (duration_seconds > 0)
            );
            INSERT OR IGNORE INTO timer_state (id, status) VALUES (1, 'idle');
            """
        )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    initialize_database()
    yield


app = FastAPI(title="Pomodoro API", version="1.0.0", lifespan=lifespan)


@app.get("/health")
def health() -> dict[str, str]:
    with database() as connection:
        connection.execute("SELECT 1")
    return {"status": "ok"}


@app.get("/api/timer", response_model=TimerResponse)
def get_timer() -> TimerResponse:
    with database(write=True) as connection:
        return current_timer(connection)


@app.post("/api/timer/start", response_model=TimerResponse)
def start_timer(payload: TimerStart | None = None) -> TimerResponse:
    payload = payload or TimerStart()
    with database(write=True) as connection:
        if current_timer(connection).status != "idle":
            raise HTTPException(status_code=409, detail="A timer is already active")
        duration = payload.duration_seconds or DEFAULT_DURATIONS[payload.kind]
        now = datetime.now(timezone.utc)
        end = now + timedelta(seconds=duration)
        connection.execute(
            """
            UPDATE timer_state
            SET status = 'running', kind = ?, started_at = ?, ends_at = ?,
                remaining_seconds = ?, duration_seconds = ?
            WHERE id = 1
            """,
            (payload.kind, now.isoformat(), end.isoformat(), duration, duration),
        )
        return current_timer(connection)


@app.post("/api/timer/pause", response_model=TimerResponse)
def pause_timer() -> TimerResponse:
    with database(write=True) as connection:
        timer = current_timer(connection)
        if timer.status != "running":
            raise HTTPException(status_code=409, detail="No running timer to pause")
        connection.execute(
            """
            UPDATE timer_state
            SET status = 'paused', ends_at = NULL, remaining_seconds = ?
            WHERE id = 1
            """,
            (timer.remaining_seconds,),
        )
        return current_timer(connection)


@app.post("/api/timer/resume", response_model=TimerResponse)
def resume_timer() -> TimerResponse:
    with database(write=True) as connection:
        timer = current_timer(connection)
        if timer.status != "paused":
            raise HTTPException(status_code=409, detail="No paused timer to resume")
        end = datetime.now(timezone.utc) + timedelta(seconds=timer.remaining_seconds)
        connection.execute(
            """
            UPDATE timer_state
            SET status = 'running', ends_at = ?
            WHERE id = 1
            """,
            (end.isoformat(),),
        )
        return current_timer(connection)


@app.post("/api/timer/stop", response_model=TimerResponse)
def stop_timer() -> TimerResponse:
    with database(write=True) as connection:
        timer = current_timer(connection)
        if timer.status != "idle":
            set_idle(connection)
        return idle_timer()


@app.get("/api/sessions", response_model=list[SessionResponse])
def list_sessions(limit: int = Query(default=50, ge=1, le=200)) -> list[SessionResponse]:
    with database() as connection:
        rows = connection.execute(
            """
            SELECT id, kind, started_at, completed_at, duration_seconds
            FROM sessions
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [SessionResponse(**dict(row)) for row in rows]


def main() -> None:
    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )
