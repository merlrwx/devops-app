# DevOps App

A study tracker built with a FastAPI API and Streamlit UI. This repository documents the path from local development through containers, CI/CD, Kubernetes, and Flux GitOps.

## Developer Quickstart

### Prerequisites

- Git, Docker with Compose, and [mise](https://mise.jdx.dev/)
- Optional: [DevPod](https://devpod.sh/) for the configured development container

Install the repository tools and sync both Python projects:

```sh
mise install
uv sync --locked --no-editable --project src/backend
uv sync --locked --no-editable --project src/frontend
```

Run the API and UI in separate terminals from the repository root:

```sh
cd src/backend && uv run study-tracker-api
```

```sh
cd src/frontend && BACKEND_URL=http://127.0.0.1:8000 \
  uv run streamlit run --server.address 0.0.0.0 app.py
```

Open <http://localhost:8501>; the API health endpoint is <http://localhost:8000/health>.

Run the test suites, coverage checks, and hooks:

```sh
(cd src/backend && uv run --locked pytest tests/ -v --cov=backend --cov-fail-under=80)
(cd src/frontend && uv run --locked pytest tests/ -v --cov=timer_utils --cov-fail-under=80)
pre-commit run --all-files
```

Or run both services with Compose:

```sh
docker compose up --build
docker compose down
```

Compose stores SQLite data in a named volume. To delete it too, run `docker compose down --volumes`.

To use the DevPod environment, run `devpod up .` then `devpod ssh`. Its setup script trusts `mise.toml` and installs the pinned tools.

## Project build path

### Module 1 — Introduction

The project is a small, practical study tracker used to build a DevOps workflow around a real application. Start with Git, Python, Docker, GitHub Actions, and Kubernetes basics.

### Module 2 — Development environment

`mise.toml` pins the development tools and exposes common Kubernetes tasks. The DevContainer runs `scripts/setup`; `scripts/setup_project` configures Git, Commitizen, and pre-commit hooks.

```sh
mise install
pre-commit install
pre-commit install --hook-type commit-msg
cz commit
```

Conventional Commit messages are checked locally and in CI. Commitizen and pre-commit are installed automatically by the DevPod project setup.

### Module 3 — Python projects

`src/backend` and `src/frontend` are independent uv projects, each with its own `pyproject.toml`, lockfile, tests, and package code. The backend is FastAPI with SQLite; the frontend is Streamlit.

The backend project was initialized as a package and its dependencies managed with uv:

```sh
cd src/backend
uv init --package backend
uv add fastapi uvicorn
uv run study-tracker-api
```

The frontend follows the same workflow. To add a dependency, use `uv add <package>` in the relevant project directory, then commit the updated lockfile. The existing frontend can be run with:

```sh
cd src/frontend
uv run streamlit run --server.address 0.0.0.0 app.py
```

The frontend exercise is to create the second uv project and connect it to the API; its implementation lives in `src/frontend`.

### Module 4 — Containers

The container exercise starts with a simple Python entry point, then packages each uv project. The checked-in Dockerfiles use pinned Alpine and uv versions, multi-stage builds, BuildKit cache mounts, and a non-root runtime user.

```sh
docker build -t devops-app-api:local -f src/backend/Dockerfile src/backend
docker build -t devops-app-web:local -f src/frontend/Dockerfile src/frontend
trivy image --scanners vuln devops-app-api:local
trivy image --scanners vuln devops-app-web:local
```

The image exercise compares Python base images, inspects image layers and size, scans with Trivy, then reduces build artifacts and runtime privileges. The frontend image solution is in `src/frontend/Dockerfile`.

### Module 5 — CI/CD with GitHub Actions

Pull requests run Ruff/pre-commit, backend and frontend tests with an 80% coverage threshold, container builds, and Trivy scans. Separate workflows keep the two service pipelines readable; path filters avoid running unrelated checks. Docker Compose remains the local integration path.

Release Please manages independent backend and frontend versions. Release tags build and publish `ghcr.io/<owner>/devops-app-api` and `ghcr.io/<owner>/devops-app-web`, then call the reusable GitOps update workflow. Development image tags update directly; production image updates are proposed as pull requests.

For repository setup, allow GitHub Actions to create pull requests and configure the required `DEVOPS_STUDY_APP` and `GITOPS_DEPLOY_KEY` secrets. The publishing workflows also need package write permission. Trivy scan steps currently report findings without failing the build.

### Module 6 — Kubernetes and Flux

The `kubernetes/` directory contains app manifests and the Python end-to-end test. k3d runs a local cluster, imports the built images, and exercises the API and frontend. The end-to-end workflow runs in GitHub Actions on relevant changes.

```sh
mise run k8s-setup-minimal
mise run k8s-setup-local
mise run e2e-test
```

The minimal cluster task creates a clean cluster; the local setup task builds and deploys the app. The E2E task creates a test cluster, verifies both services, and cleans up on success. Run `uv run --locked --project ./kubernetes python ./kubernetes/e2e_test.py --no-cleanup` to keep the cluster when diagnosing a failed run.

The GitOps repository is bootstrapped with Flux and separates reusable app manifests from environment overlays: `apps/base`, `apps/dev`, and `apps/prod`, with the development cluster entry under `clusters/dev`. Prepare a deploy key and bootstrap Flux with:

```sh
bash scripts/setup_deploy_key --visibility private
mise run setup-gitops
mise run setup-cluster-gitops
```

`setup_deploy_key` creates the GitHub GitOps repository if needed and adds its deploy key. The GitHub Actions release workflow then updates the development overlay and opens a production image update PR. A production cluster entry can be added when production Flux reconciliation is ready.
