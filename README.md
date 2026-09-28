# DevOps App

Study tracker with a FastAPI backend and a Streamlit frontend.

## CI workflow design

This repository has one frontend and one backend, so their GitHub Actions workflows are kept explicit in the repository rather than abstracted into templates. Reusable workflow templates would make sense if 100+ applications were running similar pipelines, where shared definitions would reduce repeated maintenance.

## Development environment

Start and connect to the DevPod workspace from the repository root:

```sh
devpod up .
devpod ssh
```

Initial backend project setup (already applied):

```sh
cd src/backend
uv init --package backend
uv add fastapi
```

The backend and frontend are separate uv projects. In separate terminals:

```sh
cd src/backend
uv sync --locked --no-editable
uv run study-tracker-api
```

```sh
cd src/frontend
uv sync --locked --no-editable
BACKEND_URL=http://127.0.0.1:8000 uv run streamlit run --server.address 0.0.0.0 app.py
```

The API listens on port 8000 and Streamlit on port 8501.

## Container image exercise

Build the backend and frontend separately. Run these commands from the repository root; each build context is its service directory.

### 1. Build the `python:latest` baseline

For the baseline exercise, use a single-stage Dockerfile with `FROM python:latest`, install uv, copy the service files, run `uv sync`, and keep that service's application `CMD`. Build and inspect each image:

```sh
docker build --progress=plain \
  -t devops-app-backend:python-latest \
  -f src/backend/Dockerfile src/backend

docker build --progress=plain \
  -t devops-app-frontend:python-latest \
  -f src/frontend/Dockerfile src/frontend

docker image inspect --format '{{.RepoTags}} {{.Size}} bytes' \
  devops-app-backend:python-latest devops-app-frontend:python-latest

docker history --no-trunc devops-app-backend:python-latest
docker history --no-trunc devops-app-frontend:python-latest

mise exec trivy@0.74.0 -- trivy image --scanners vuln devops-app-backend:python-latest
mise exec trivy@0.74.0 -- trivy image --scanners vuln devops-app-frontend:python-latest
```

`docker history` shows the image's layers and their sizes. Each `RUN`, `COPY`, and `ADD` instruction in a single-stage Dockerfile adds a layer. Trivy findings depend on the vulnerability database version and change over time.

### 2. Compare slim and Alpine bases

Repeat the build, size, layer, and Trivy commands after changing the Python base image in each Dockerfile. Give each iteration a different tag so the images can be compared side by side:

| Iteration | Python base | Example image tags |
| --- | --- | --- |
| Baseline | `python:latest` | `devops-app-backend:python-latest`, `devops-app-frontend:python-latest` |
| Slim | `python:3.13.15-slim` | `devops-app-backend:slim`, `devops-app-frontend:slim` |
| Alpine | `python:3.13.15-alpine3.24` | `devops-app-backend:alpine`, `devops-app-frontend:alpine` |

For each iteration, replace the image tag in the build commands and in both Python `FROM` lines if the Dockerfile has builder and runtime stages. Then repeat `docker image inspect`, `docker history`, and both `trivy image` commands with the new tags.

Alpine uses musl instead of glibc. Confirm that every Python dependency has a compatible wheel and run the image before choosing it. The frontend includes large scientific packages, so its total image size will remain larger than the backend image.

### 3. Use a multi-stage build

The service Dockerfiles use a builder stage to install dependencies and build the project. The runtime stage starts from the same pinned Python image and copies only the virtual environment and files needed to run the service. This keeps build tools and intermediate files out of the final image.

Backend runtime command:

```dockerfile
CMD ["/app/.venv/bin/study-tracker-api"]
```

Frontend runtime command:

```dockerfile
CMD ["/app/.venv/bin/streamlit", "run", "--server.address", "0.0.0.0", "/app/app.py"]
```

The build, size inspection, layer inspection, and scan steps are the same for both services; each image uses its own runtime command.

### 4. Cache uv downloads during builds

Both Dockerfiles mount the uv cache for dependency installation and bind `uv.lock` plus `pyproject.toml` before copying the source. This lets BuildKit reuse dependency downloads and the dependency layer when only application code changes. The `--progress=plain` build commands above show whether the layer was cached.

### Build the current images

The checked-in Dockerfiles use pinned Alpine and uv versions, multi-stage builds, cache mounts, and a non-root runtime user with UID/GID 1000.

```sh
docker build --progress=plain \
  -t devops-app-backend:alpine \
  -f src/backend/Dockerfile src/backend

docker build --progress=plain \
  -t devops-app-frontend:alpine \
  -f src/frontend/Dockerfile src/frontend
```

Inspect final image sizes and layers:

```sh
docker image inspect --format '{{.RepoTags}} {{.Size}} bytes' \
  devops-app-backend:alpine devops-app-frontend:alpine

docker history --no-trunc devops-app-backend:alpine
docker history --no-trunc devops-app-frontend:alpine
```

The images built for this exercise were 20,335,941 bytes for the backend and 133,275,615 bytes for the frontend. Rebuild and inspect after each base-image or Dockerfile change to compare your results.

Scan both images with Trivy after building them:

```sh
mise exec trivy@0.74.0 -- trivy version

mise exec trivy@0.74.0 -- trivy image --scanners vuln --format json \
  --output /tmp/devops-app-backend-trivy.json devops-app-backend:alpine

mise exec trivy@0.74.0 -- trivy image --scanners vuln --format json \
  --output /tmp/devops-app-frontend-trivy.json devops-app-frontend:alpine
```

The 2026-09-27 scan found the same three Python-package advisories in both images: two HIGH and one MEDIUM. Findings were for `msgpack` (`GHSA-6v7p-g79w-8964`) and `setuptools` (`CVE-2025-47273`, `CVE-2026-59890`). Rerun the commands for current results.

### Run the images

From the repository root, Compose builds and starts both services on a shared network. It waits for the API health check before starting the frontend and keeps SQLite data in a named volume:

```sh
docker compose up --build
```

Open <http://localhost:8501> for the frontend or <http://localhost:8000/health> for the API health check. Stop the services with Ctrl-C, or run:

```sh
docker compose down
```

The backend database volume remains after `docker compose down`. Remove it only when you also want to delete stored sessions:

```sh
docker compose down --volumes
```
