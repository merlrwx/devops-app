## Pomodoro API

Install the locked dependencies and start the API from this directory:

    uv sync
    uv run study-tracker-api

The API listens on port 8000 by default. Interactive API documentation is at
/docs. Uvicorn writes request and runtime logs to the process output stream.
Run the Python frontend separately from `src/frontend`; it listens on port 8501
and calls this API at `http://127.0.0.1:8000` by default.

### Configuration

All runtime settings come from environment variables:

| Variable | Default | Purpose |
| --- | --- | --- |
| HOST | 0.0.0.0 | Address to bind |
| PORT | 8000 | Port to bind |
| DATABASE_PATH | data/pomodoro.sqlite3 | SQLite database file |
| FOCUS_SECONDS | 1500 | Default focus timer |
| SHORT_BREAK_SECONDS | 300 | Default short break |
| LONG_BREAK_SECONDS | 900 | Default long break |

Durations must be between 1 second and 3 hours. Set DATABASE_PATH to a
persistent mounted volume when running in a container.

### API

- GET /health checks that the API and database are available.
- GET /api/timer returns the current timer and remaining time.
- POST /api/timer/start starts a focus, short break, or long break timer.
  Send {"kind":"focus"} to use the configured default or include
  duration_seconds to override it.
- POST /api/timer/pause, POST /api/timer/resume, and
  POST /api/timer/stop change the active timer.
- GET /api/sessions lists completed timers, newest first. An expired timer
  is recorded when its state is next requested.

The process keeps no timer state in memory, so restarts preserve active timers
and session history. SQLite is a local file backing service: deploy it with a
persistent volume on one host, and use a network database before scaling across
hosts.

### Container

Trivy is managed by mise. Run `mise install` if it is not installed, then build
and scan the image from the repository root:

    docker build -t backend:00 -f src/backend/Dockerfile src/backend
    TAG=00 && trivy image --format table --severity CRITICAL,HIGH backend:$TAG

Run with a named volume so the SQLite database survives container removal:

    docker run --rm -p 8000:8000 -v pomodoro-data:/app/data backend:00
