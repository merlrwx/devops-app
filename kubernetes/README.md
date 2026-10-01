# Local Kubernetes development

## Setup and sync

```sh
mise run k8s-setup-local
```

Setup keeps the existing interactive workflow: create the cluster if missing,
or choose whether to reuse or delete and recreate it. It selects the cluster's
kubectl context, deploys the app, displays service URLs, and prints port-forwarding
instructions.

After editing the app or manifests, use the new command:

```sh
mise run k8s-sync-local
```

Sync updates an existing `devops-app-cluster`. It never creates or deletes a
cluster, prompts for recreation, or changes your current kubectl context. Setup
calls the same deployment script so build/import/apply steps are maintained in
one place.

The script builds both images using Docker's cache, assigns tags from their
Docker image IDs, imports them into k3d, and applies a temporary Kustomize render.
Changed image tags trigger rollouts; unchanged images keep their Pods. Manifest
changes are applied each time, and checked-in image tags are never rewritten.
Local builds omit timestamped provenance attestations to keep cached image IDs
stable. Published CI images retain their existing build settings.

SQLite remains inside the backend container. Backend Pod or container replacement
can lose timer/session data; save anything needed before updating it.

## Flux deployments

Setup and GitOps currently use the same cluster name. Local deployment refuses a
cluster containing `flux-system`, because Flux could overwrite direct changes.
Use the GitOps workflow for that cluster, or explicitly choose to recreate it
through local setup when switching to direct deployment.

If the separate GitOps repository maintains its own manifests, adopt these probe
changes there separately; image publishing does not copy these YAML changes.

## Health probes

Backend readiness uses the existing SQLite-backed `/health` on port 8000.
Startup and liveness use `/live`, which responds without accessing SQLite.
Database failure can remove the backend from service traffic without causing
liveness restarts. Frontend probes use `/_stcore/health` on port 8501 and do not
check backend connectivity.

Startup allows approximately two minutes. Readiness runs every five seconds;
liveness runs every ten seconds and restarts after three consecutive failures.
HTTP probe timeouts are two seconds.

## Operating the local cluster

```sh
mise run k8s-status
mise run k8s-logs backend    # or frontend; Ctrl-C stops following
mise run k8s-down-local
```

Rerun the logs command after a rollout replaces the Pod being followed.

Status and logs explicitly target `k3d-devops-app-cluster` and namespace
`devops-app`, regardless of your current kubectl context. Teardown deletes only
`devops-app-cluster`, including application data and any installed Flux resources.
An already absent cluster is a successful no-op. It leaves `devops-app-minimal`,
images, deploy keys and repository files alone. Setup can still delete and recreate
the application cluster interactively.

## Reproducible cluster startup

All setup modes and E2E use `k3d-config.yaml`, pinned to
`rancher/k3s:v1.35.8-k3s1`. The minimal task overrides the name and topology to one
server, no agents and no load balancer; it deploys no application workloads.
Developer tools and E2E CI both use k3d 5.9.0.

Setup checks Docker access before creating a cluster, then waits up to 120 seconds
each for ready nodes, CoreDNS creation and its rollout. GitOps performs these checks
before building images or bootstrapping Flux. Existing clusters retain their
Kubernetes version until you explicitly recreate them.

For upgrades, select a stable [k3s release](https://docs.k3s.io/release-notes/v1.35.X)
compatible with [Flux's prerequisites](https://fluxcd.io/flux/installation/),
update the image pin and run E2E against a disposable cluster. Keep the k3d tool
pin and CI installer version aligned.

## Verification

The existing `mise run e2e-test` workflow remains in place. It recreates
`devops-app-cluster`, tests both applications, and deletes the cluster on success.
Use a disposable environment rather than a cluster containing needed data.

Guard and rendering checks also cover setup's reuse and recreation choices:

```sh
uv run --locked --project kubernetes python -m unittest discover -s kubernetes/tests -v
```

These checks require installed kubectl but no running cluster. CI runs them before
the existing E2E command.
