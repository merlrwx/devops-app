# DevOps App

A study tracker built with a FastAPI API and Streamlit UI. This repository documents the path from local development through containers, CI/CD, Kubernetes, and Flux GitOps.

## Developer Quickstart

### Prerequisites

- Docker and [DevPod](https://devpod.sh/)
- GitHub CLI authenticated to an account that can create/use the GitOps repository

Start the configured workspace, then install the tools pinned in `mise.toml`:

```sh
devpod up .
devpod ssh
mise install
```

Inside the workspace, set its DevPod ID (used to store the deploy key) and authenticate GitHub CLI if needed:

```sh
export DEVPOD_WORKSPACE_ID=devops-app
gh auth login
```

Create the local k3d cluster and bootstrap Flux from the configured GitOps repository:

```sh
mise run setup-cluster-gitops
```

The task builds and imports the app images, creates the GitHub deploy key/repository if needed, and bootstraps Flux to reconcile `clusters/dev`. Check deployments with `kubectl get pods -n devops-app`.

Forward the services in separate terminals, then open <http://localhost:8501>:

```sh
kubectl port-forward svc/dev-frontend -n devops-app 8501:22111
```

```sh
kubectl port-forward svc/dev-backend -n devops-app 8000:22112
```

Sync the projects and run the test suites when working on app code:

```sh
uv sync --locked --no-editable --project src/backend
uv sync --locked --no-editable --project src/frontend
uv run --locked --project src/backend pytest tests/ -v --cov=backend --cov-fail-under=80
uv run --locked --project src/frontend pytest tests/ -v --cov=timer_utils --cov-fail-under=80
pre-commit run --all-files
```

The E2E check is available as `mise run e2e-test`. Delete the local application cluster when finished with `mise run k8s-down-local`.

### Native development with reload

Run these commands in separate terminals:

```sh
mise run dev-backend
mise run dev-frontend
```

Open <http://127.0.0.1:8501>; the API is at <http://127.0.0.1:8000>.
Both tasks use their service directory and locked uv dependencies. Backend reload
watches only `src/backend/src/backend`; SQLite defaults to
`src/backend/data/pomodoro.sqlite3`, outside that watch directory. Set
`DATABASE_PATH` to choose another database. Streamlit automatically reruns active
sessions after frontend edits and uses the local API. Ctrl-C stops each server.
These commands run without Docker or Kubernetes.

For the local application cluster, `mise run k8s-status`, `mise run k8s-logs backend`
(or `frontend`), and `mise run k8s-console` provide status, logs and k9s. Use
`mise run k8s-down-local` to delete that cluster and its data. See
[the Kubernetes workflow](kubernetes/README.md) for startup checks and version pins.

## Project build path

### Module 1 — Introduction

The project is a small, practical study tracker used to build a DevOps workflow around a real application. Start with Git, Python, Docker, GitHub Actions, and Kubernetes basics.

### Module 2 — Development environment

`mise.toml` pins the development tools, including pre-commit, and exposes common Kubernetes tasks. `mise install` installs those tools, and the DevContainer activates them in Bash and Zsh, so run `uv` and `pre-commit` directly. The DevContainer runs `scripts/setup`; `scripts/setup_project` configures Git, Commitizen, and pre-commit hooks.

Installing the pre-commit executable and registering its Git hooks are separate steps. The commands below register hooks in the current checkout; the DevPod setup does this automatically.

```sh
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

Pull requests run Ruff/pre-commit, backend and frontend tests with an 80% coverage threshold, container builds, and Trivy scans. Separate workflows keep the two service pipelines readable; path filters avoid running unrelated checks. The local development deployment uses k3d and Flux; Compose is available for a quick container-only smoke run.

Release Please manages independent backend and frontend versions. Release tags build and publish `ghcr.io/<owner>/devops-app-api` and `ghcr.io/<owner>/devops-app-web`, then call the reusable GitOps update workflow. Development image tags update directly; production image updates are proposed as pull requests.

For repository setup, allow GitHub Actions to create pull requests and configure the required `DEVOPS_STUDY_APP` and `GITOPS_DEPLOY_KEY` secrets. The publishing workflows also need package write permission. Trivy scan steps currently report findings without failing the build.

### Module 6 — Kubernetes and Flux

The `kubernetes/` directory contains app manifests and the Python end-to-end test. k3d runs a local cluster, imports the built images, and exercises the API and frontend. The end-to-end workflow runs in GitHub Actions on relevant changes.

```sh
mise run k8s-setup-minimal
mise run k8s-setup-local
mise run k8s-sync-local
mise run e2e-test
```

The minimal cluster task creates a clean cluster; the local setup task builds and deploys the app. The E2E task creates a test cluster, verifies both services, and cleans up on success. Run `uv run --locked --project ./kubernetes python ./kubernetes/e2e_test.py --no-cleanup` to keep the cluster when diagnosing a failed run.

Local setup retains its delete/recreate prompt and service URL output. The new `k8s-sync-local` command updates an existing cluster after edits without recreating it. See [the Kubernetes workflow](kubernetes/README.md) for image tags, health probes, and the Flux boundary.

The GitOps repository is bootstrapped with Flux and separates reusable app manifests from environment overlays: `apps/base`, `apps/dev`, and `apps/prod`, with the development cluster entry under `clusters/dev`. Prepare a deploy key and bootstrap Flux with:

```sh
mise run setup-keys
mise run setup-gitops
mise run setup-cluster-gitops
```

`setup_deploy_key` creates the GitHub GitOps repository if needed and adds its deploy key. `setup-cluster-gitops` combines local cluster creation and Flux bootstrap. The GitHub Actions release workflow updates the development overlay and opens a production image update PR. A production cluster entry can be added when production Flux reconciliation is ready.
