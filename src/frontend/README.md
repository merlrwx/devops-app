# Pomodoro frontend

This is a Python Streamlit app. Install its locked dependencies with uv:

    uv sync

Start the backend in one terminal, from this directory:

    cd ../backend
    uv sync
    uv run study-tracker-api

In a second terminal, from this directory, start the frontend:

    uv run streamlit run --server.address 0.0.0.0 app.py

Open http://localhost:8501. The frontend reads the API address from
`BACKEND_URL`, which defaults to `http://127.0.0.1:8000`.

For a backend running in another container, set `BACKEND_URL` to its reachable
service URL, for example:

    BACKEND_URL=http://backend:8000 uv run streamlit run --server.address 0.0.0.0 app.py
