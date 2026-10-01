# Local Kubernetes development

## Direct local deployment

```sh
mise run k8s-setup-local
# After editing app code, dependencies, Dockerfiles, or manifests:
mise run k8s-sync-local
```

Setup creates `devops-app-cluster` if missing and reuses it otherwise. Sync requires
that cluster to exist. Both commands check Docker access and target the explicit
`k3d-devops-app-cluster` context, regardless of your current kubectl context.

Sync builds both services using Docker's cache, tags each built image with
`local-<image-id>`, imports the images into k3d, and applies a temporary Kustomize
render with those tags. An unchanged built image keeps its tag, so it does not
trigger another rollout. Local builds omit timestamped provenance attestations
to keep IDs stable; published CI images use their existing build workflow. Manifest changes are applied on every sync. The
checked-in dev overlay is never rewritten. Temporary files are removed on exit.
Image IDs identify the built local artifacts; they are not registry digests or a
guarantee that independent builds produce identical bytes.

**Data is temporary:** SQLite lives inside the backend container. Replacing its
Pod or restarting its container can lose timers and session history. Save any
needed data before syncing backend changes. This workflow does not add persistent
storage or delete existing clusters automatically.

Access the services in separate terminals:

```sh
kubectl --context k3d-devops-app-cluster port-forward svc/dev-frontend -n devops-app 8501:22111
kubectl --context k3d-devops-app-cluster port-forward svc/dev-backend -n devops-app 8000:22112
```

Open <http://localhost:8501> or <http://localhost:8000/health>.

## Flux deployments

Direct local setup and GitOps setup currently share the same cluster name.
Setup/sync refuse clusters containing `flux-system` before building or applying
workloads. Use `mise run setup-cluster-gitops` and changes in the GitOps repository
for that cluster. To switch modes, explicitly delete the local cluster only after
saving needed data, then run the desired setup task. Sync never suspends Flux.

These base manifests add probes to direct local deployments. If the GitOps
repository maintains its own copies of the manifests, update those copies
separately to adopt the probes there; image publishing does not copy these YAML
changes automatically.

## Health probes

Backend readiness uses `/health` on port 8000, including its existing SQLite
check. Startup and liveness use `/live`, which checks that the API can respond
without accessing SQLite. A temporary database failure removes the backend from
service traffic without causing a liveness restart. Frontend probes use
`/_stcore/health` on port 8501; they do not test backend connectivity.

Startup permits approximately two minutes of initialization before regular
probes begin. Readiness runs every five seconds; liveness runs every ten seconds
and restarts after three consecutive failures. HTTP probe timeouts are two seconds.

## Verification

`mise run e2e-test` exercises both applications using the same sync script.
**It recreates `devops-app-cluster` and deletes it on success.** Use a separate
cluster name to preserve your development cluster:

```sh
uv run --locked --project kubernetes python kubernetes/e2e_test.py --cluster-name devops-app-e2e
```

Guard and real Kustomize-render checks need installed kubectl but no running
cluster:

```sh
uv run --locked --project kubernetes python -m unittest discover -s kubernetes/tests -v
```

The full workflow check creates and always removes its own uniquely named k3d
cluster, copies sources into a temporary directory, and uses a temporary kubeconfig.
It verifies cold startup, unchanged sync, service-only edits, manifest-only edits,
context isolation, Flux rejection, and readiness/liveness failure behavior:

```sh
uv run --locked --project kubernetes python kubernetes/verify_local_workflow.py
```

CI runs both the guard checks and the full workflow check on relevant changes.

It requires a working Docker daemon, k3d, and kubectl. No extra Python dependency
is needed. It leaves built Docker images available for cache reuse.
