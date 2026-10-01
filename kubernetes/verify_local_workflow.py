"""Verify sync and probes in a disposable k3d cluster; never edit the checkout."""

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from pathlib import Path

import e2e_test as e2e


def deployment(app):
    return json.loads(
        e2e.kubectl(
            "get",
            "deployment",
            f"dev-{app}",
            "-n",
            e2e.NAMESPACE,
            "-o",
            "json",
            capture_output=True,
        ).stdout
    )


def pod(app):
    result = e2e.kubectl(
        "get",
        "pods",
        "-n",
        e2e.NAMESPACE,
        "-l",
        f"component={app}",
        "-o",
        "json",
        capture_output=True,
    )
    pods = json.loads(result.stdout)["items"]
    active = [item for item in pods if not item["metadata"].get("deletionTimestamp")]
    e2e.require(len(active) == 1, f"Expected one active {app} Pod")
    return active[0]


def wait_for(check, message, timeout=120):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(2)
    raise AssertionError(message)


def ready(item):
    return any(
        condition["type"] == "Ready" and condition["status"] == "True"
        for condition in item["status"].get("conditions", [])
    )


def restarts(item):
    return item["status"]["containerStatuses"][0]["restartCount"]


def snapshot():
    return {
        app: (deployment(app)["spec"]["template"], pod(app)["metadata"]["uid"])
        for app in ("backend", "frontend")
    }


def verify_probes(backend_url):
    for app, port, live, health in (
        ("backend", 8000, "/live", "/health"),
        ("frontend", 8501, "/_stcore/health", "/_stcore/health"),
    ):
        container = deployment(app)["spec"]["template"]["spec"]["containers"][0]
        for kind, path in (
            ("startup", live),
            ("readiness", health),
            ("liveness", live),
        ):
            e2e.require(
                container[f"{kind}Probe"]["httpGet"]
                == {"path": path, "port": port, "scheme": "HTTP"},
                f"Incorrect {app} {kind} probe",
            )
        e2e.require(restarts(pod(app)) == 0, f"{app} restarted during cold startup")

    original = pod("backend")
    name = original["metadata"]["name"]
    # Making the SQLite filename a directory blocks health without corrupting data.
    e2e.kubectl(
        "exec",
        name,
        "-n",
        e2e.NAMESPACE,
        "--",
        "python",
        "-c",
        "from pathlib import Path; p=Path('/app/data/pomodoro.sqlite3'); p.rename(p.with_suffix('.saved')); p.mkdir()",
    )
    try:
        wait_for(lambda: not ready(pod("backend")), "Readiness did not fail")
        endpoints = json.loads(
            e2e.kubectl(
                "get",
                "endpointslices",
                "-n",
                e2e.NAMESPACE,
                "-l",
                "kubernetes.io/service-name=dev-backend",
                "-o",
                "json",
                capture_output=True,
            ).stdout
        )
        e2e.require(
            not any(
                endpoint["conditions"].get("ready")
                for item in endpoints["items"]
                for endpoint in item.get("endpoints", [])
            ),
            "Unready backend still receives service traffic",
        )
        # Observe beyond the liveness failure window; SQLite failure must not restart.
        deadline = time.monotonic() + 35
        while time.monotonic() < deadline:
            current = pod("backend")
            e2e.require(
                current["metadata"]["uid"] == original["metadata"]["uid"]
                and restarts(current) == restarts(original),
                "Database failure restarted backend",
            )
            time.sleep(2)
    finally:
        e2e.kubectl(
            "exec",
            name,
            "-n",
            e2e.NAMESPACE,
            "--",
            "python",
            "-c",
            "from pathlib import Path; p=Path('/app/data/pomodoro.sqlite3'); p.rmdir(); p.with_suffix('.saved').rename(p)",
        )
    wait_for(lambda: ready(pod("backend")), "Readiness did not recover")
    e2e.api(backend_url, "/live")

    # A failing liveness URL exercises kubelet restarts without PID 1 signal quirks.
    for path in ("/__liveness_failure", "/live"):
        patch = [
            {
                "op": "replace",
                "path": "/spec/template/spec/containers/0/livenessProbe/httpGet/path",
                "value": path,
            }
        ]
        e2e.kubectl(
            "patch",
            "deployment",
            "dev-backend",
            "-n",
            e2e.NAMESPACE,
            "--type=json",
            "-p",
            json.dumps(patch),
        )
        e2e.kubectl(
            "rollout",
            "status",
            "deployment/dev-backend",
            "-n",
            e2e.NAMESPACE,
            "--timeout=180s",
        )
        if path != "/live":
            wait_for(
                lambda: restarts(pod("backend")) > 0,
                "Failed liveness did not restart backend",
            )
    e2e.wait_for_service(f"{backend_url}/health")


def main():
    e2e.CLUSTER_NAME = f"devops-app-verify-{uuid.uuid4().hex[:8]}"
    with tempfile.TemporaryDirectory(prefix="devops-app-verify-") as directory:
        directory = Path(directory)
        # Keep k3d's kubeconfig changes and all source/manifest mutations temporary.
        os.environ["KUBECONFIG"] = str(directory / "kubeconfig")
        repo = directory / "repo"
        shutil.copytree(
            e2e.ROOT_DIR / "src",
            repo / "src",
            ignore=shutil.ignore_patterns(
                ".venv", "__pycache__", ".pytest_cache", "data", ".ruff_cache"
            ),
        )
        shutil.copytree(
            e2e.KUBERNETES_DIR / "manifests", repo / "kubernetes" / "manifests"
        )
        for name in ("sync_local", "k3d-config.yaml"):
            shutil.copy(e2e.KUBERNETES_DIR / name, repo / "kubernetes" / name)
        e2e.KUBERNETES_DIR = repo / "kubernetes"
        try:
            e2e.setup_cluster(False)
            # A deliberately unrelated current context exposes implicit-context commands.
            e2e.run("kubectl", "config", "set", "current-context", "unrelated-context")
            backend_url, frontend_url = e2e.deploy_application()
            e2e.wait_for_service(f"{backend_url}/health")
            e2e.wait_for_service(f"{frontend_url}/_stcore/health")
            e2e.test_backend(backend_url)
            e2e.test_frontend(frontend_url)
            initial = snapshot()
            e2e.deploy_application()
            e2e.require(snapshot() == initial, "Unchanged sync rolled out a workload")
            for app, relative in (
                ("backend", "src/backend/src/backend/main.py"),
                ("frontend", "src/frontend/app.py"),
            ):
                before = snapshot()
                source = repo / relative
                source.write_text(
                    source.read_text() + "\n# Local sync verification change.\n"
                )
                e2e.deploy_application()
                after = snapshot()
                other = "frontend" if app == "backend" else "backend"
                e2e.require(
                    after[app][0]["spec"]["containers"][0]["image"]
                    != before[app][0]["spec"]["containers"][0]["image"],
                    f"{app} image did not change",
                )
                e2e.require(
                    after[app][1] != before[app][1] and after[other] == before[other],
                    f"{app} edit rolled out the wrong workloads",
                )
            namespace = repo / "kubernetes/manifests/dev/namespace.yaml"
            namespace.write_text(
                namespace.read_text().replace(
                    "metadata:\n",
                    "metadata:\n  annotations:\n    local-sync-check: applied\n",
                )
            )
            before = snapshot()
            e2e.deploy_application()
            annotations = json.loads(
                e2e.kubectl(
                    "get", "namespace", e2e.NAMESPACE, "-o", "json", capture_output=True
                ).stdout
            )["metadata"]["annotations"]
            e2e.require(
                annotations["local-sync-check"] == "applied" and snapshot() == before,
                "Manifest-only sync failed or rolled out workloads",
            )
            verify_probes(backend_url)
            # A real Flux marker must stop sync before build or workload changes.
            e2e.kubectl("create", "namespace", "flux-system")
            result = subprocess.run(
                ["bash", str(e2e.KUBERNETES_DIR / "sync_local"), e2e.CLUSTER_NAME],
                capture_output=True,
                text=True,
                check=False,
            )
            e2e.require(
                result.returncode != 0
                and "GitOps workflow" in result.stderr
                and "Building" not in result.stdout,
                "Flux guard failed",
            )
            print(
                "Cold E2E, repeated sync, service edits, manifest edits, context isolation, probes and Flux guard passed."
            )
        finally:
            e2e.run("k3d", "cluster", "delete", e2e.CLUSTER_NAME)


if __name__ == "__main__":
    main()
