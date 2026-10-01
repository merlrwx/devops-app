"""Exercise local command guards and rendering without Docker or a cluster."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# Only the external side effects are stubbed; Kustomize rendering is real.
FAKE_TOOL = """#!/usr/bin/env python3
import json
import os
from pathlib import Path
import subprocess
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
with open(os.environ["CALL_LOG"], "a") as log:
    log.write(json.dumps([name, *args]) + "\\n")
mode = os.environ.get("FAIL_AT", "")
if name == "docker":
    if args[0] == "info" and mode == "daemon":
        sys.exit(1)
    if args[0] == "build":
        if mode == "build":
            sys.exit(1)
    if args[:2] == ["image", "inspect"]:
        app = args[2].split(":")[0]
        print("sha256:" + ("a" if app == "backend" else "b") * 64)
elif name == "k3d":
    if args[:2] == ["cluster", "list"]:
        if mode == "cluster-list":
            sys.exit(1)
        state = Path(os.environ["CLUSTER_STATE"])
        exists = state.read_text() == "present" if state.exists() else mode != "missing-cluster"
        if "--no-headers" not in args:
            print("NAME SERVERS AGENTS LOADBALANCER")
        print(("devops-app-cluster" if exists else "devops-app-cluster-other") + " 1 1 true")
    elif args[:2] == ["cluster", "delete"]:
        Path(os.environ["CLUSTER_STATE"]).write_text("missing")
    elif args[:2] == ["cluster", "create"]:
        Path(os.environ["CLUSTER_STATE"]).write_text("present")
    elif args[:2] == ["image", "import"] and mode == "import":
        sys.exit(1)
elif name == "kubectl":
    if args[:2] == ["--context", "k3d-devops-app-cluster"]:
        command = args[2:]
    else:
        command = args
        if command[:3] == ["config", "use-context", "k3d-devops-app-cluster"]:
            sys.exit(0)
        if command[0] == "wait" or command[:2] == ["--namespace", "kube-system"]:
            if mode == "readiness" or (mode == "dns" and "deployment/coredns" in command) or (mode == "dns-rollout" and "rollout" in command):
                sys.exit("Cluster readiness failed")
            sys.exit(0)
        if command[:2] == ["get", "services"]:
            sys.exit(0)
        if command[:2] == ["get", "svc"]:
            if "loadBalancer" in command[-1]:
                print("127.0.0.1")
            else:
                print("22111" if command[2] == "dev-frontend" else "22112")
            sys.exit(0)
        sys.exit("Missing explicit context")
    if command[:3] == ["get", "namespace", "flux-system"]:
        if mode == "api":
            sys.exit("API unavailable")
        if mode == "flux":
            print("namespace/flux-system")
    elif command[0] == "kustomize":
        sys.exit(subprocess.call([os.environ["REAL_KUBECTL"], *args]))
    elif command[0] == "apply":
        Path(os.environ["APPLIED_MANIFEST"]).write_text(Path(command[2]).read_text())
"""


class SyncLocalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.repo = self.directory / "repo with spaces"
        manifests = self.repo / "kubernetes" / "manifests"
        shutil.copytree(ROOT / "kubernetes" / "manifests", manifests)
        for script in (
            "sync_local",
            "setup_cluster_local",
            "setup_cluster_minimal",
            "logs",
            "down_local",
        ):
            shutil.copy(ROOT / "kubernetes" / script, manifests.parent / script)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        for name in ("docker", "k3d", "kubectl", "flux"):
            tool = self.bin / name
            tool.write_text(FAKE_TOOL)
            tool.chmod(0o755)
        self.work = self.directory / "work"
        self.work.mkdir()
        self.log = self.directory / "calls.jsonl"
        self.applied = self.directory / "applied.yaml"
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "TMPDIR": str(self.work),
            "CALL_LOG": str(self.log),
            "CLUSTER_STATE": str(self.directory / "cluster-state"),
            "APPLIED_MANIFEST": str(self.applied),
            "REAL_KUBECTL": shutil.which("kubectl"),
            "KUBECONFIG": str(self.directory / "unused-kubeconfig"),
        }

    def run_script(self, mode="", script="sync_local", *args, answer="n"):
        result = subprocess.run(
            ["bash", str(self.repo / "kubernetes" / script), *args],
            env={**self.env, "FAIL_AT": mode},
            input=f"{answer}\n",
            text=True,
            capture_output=True,
            check=False,
        )
        calls = (
            [json.loads(line) for line in self.log.read_text().splitlines()]
            if self.log.exists()
            else []
        )
        self.assertEqual(list(self.work.iterdir()), [], "Temporary files leaked")
        return result, calls

    def test_logs_validate_service_before_calling_kubectl(self):
        for args in ((), ("other",), ("backend", "frontend"), ("--all",)):
            with self.subTest(args=args):
                self.log.write_text("")
                result, calls = self.run_script("", "logs", *args)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("Usage:", result.stderr)
                self.assertEqual(calls, [])
        for service in ("backend", "frontend"):
            self.log.write_text("")
            result, calls = self.run_script("", "logs", service)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                calls,
                [
                    [
                        "kubectl",
                        "--context",
                        "k3d-devops-app-cluster",
                        "--namespace",
                        "devops-app",
                        "logs",
                        f"deployment/dev-{service}",
                        "--follow",
                        "--tail=100",
                        "--all-containers=true",
                    ]
                ],
            )

    def test_down_only_deletes_exact_application_cluster(self):
        result, calls = self.run_script(script="down_local")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(["k3d", "cluster", "delete", "devops-app-cluster"], calls)
        self.log.write_text("")
        result, calls = self.run_script(script="down_local")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("already absent", result.stdout)
        self.assertEqual(calls, [["k3d", "cluster", "list", "--no-headers"]])

    def test_down_stops_on_list_failure_or_arguments(self):
        for mode, args in (("cluster-list", ()), ("", ("--all",))):
            self.log.write_text("")
            result, calls = self.run_script(mode, "down_local", *args)
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(
                any(call[:3] == ["k3d", "cluster", "delete"] for call in calls)
            )

    def test_readiness_failure_stops_setup_before_build(self):
        for mode in ("readiness", "dns", "dns-rollout"):
            self.log.write_text("")
            result, calls = self.run_script(mode, "setup_cluster_local")
            self.assertNotEqual(result.returncode, 0)
            self.assertFalse(any(call[:2] == ["docker", "build"] for call in calls))

    def test_minimal_setup_requires_usable_docker(self):
        result, calls = self.run_script("daemon", "setup_cluster_minimal")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker daemon is unavailable", result.stderr)
        self.assertEqual(calls, [["docker", "info"]])

    def test_gitops_preflights_stop_before_build_or_bootstrap(self):
        for mode in ("daemon", "readiness", "dns", "dns-rollout"):
            with self.subTest(mode=mode):
                self.log.write_text("")
                result = subprocess.run(
                    ["bash", str(ROOT / "scripts" / "setup_cluster_gitops")],
                    env={
                        **self.env,
                        "FAIL_AT": mode,
                        "DEVPOD_WORKSPACE_ID": "verification",
                        "GITUSER": "unused",
                        "GITOPS_REPO": "unused",
                    },
                    text=True,
                    capture_output=True,
                    check=False,
                )
                self.assertNotEqual(result.returncode, 0)
                calls = [json.loads(line) for line in self.log.read_text().splitlines()]
                self.assertFalse(any(call[:2] == ["docker", "build"] for call in calls))
                self.assertFalse(any(call[0] == "flux" for call in calls))
                if mode == "daemon":
                    self.assertIn("Docker daemon is unavailable", result.stderr)
                    self.assertEqual(calls, [["docker", "info"]])

    def test_invalid_cluster_name_fails_before_external_commands(self):
        result, calls = self.run_script("", "sync_local", "--all")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Usage:", result.stderr)
        self.assertEqual(calls, [])

    def test_guards_fail_before_build_or_mutation(self):
        for mode, message in (
            ("daemon", "Docker daemon is unavailable"),
            ("missing-cluster", "k8s-setup-local"),
            ("cluster-list", ""),
            ("flux", "GitOps workflow"),
            ("api", "API unavailable"),
        ):
            with self.subTest(mode=mode):
                self.log.write_text("")
                result, calls = self.run_script(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertFalse(any(call[:2] == ["docker", "build"] for call in calls))
                self.assertFalse(self.applied.exists())

    def test_build_or_import_failure_never_applies_partial_deployment(self):
        for mode in ("build", "import"):
            with self.subTest(mode=mode):
                self.log.write_text("")
                result, _ = self.run_script(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(self.applied.exists())

    def test_setup_reuses_cluster_and_sync_applies_immutable_images_once(self):
        result, calls = self.run_script(script="setup_cluster_local")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(
            any(
                call[:2] == ["k3d", "cluster"] and call[2] in ("create", "delete")
                for call in calls
            )
        )
        applies = [
            call
            for call in calls
            if call[:4] == ["kubectl", "--context", "k3d-devops-app-cluster", "apply"]
        ]
        self.assertEqual(len(applies), 1)
        manifest = self.applied.read_text()
        for app, digest in (("backend", "a" * 64), ("frontend", "b" * 64)):
            self.assertIn(f"image: {app}:local-{digest}", manifest)
        self.assertIn(
            [
                "k3d",
                "image",
                "import",
                f"backend:local-{'a' * 64}",
                f"frontend:local-{'b' * 64}",
                "--cluster",
                "devops-app-cluster",
                "--mode",
                "direct",
            ],
            calls,
        )
        self.assertIn("Using existing cluster.", result.stdout)
        output = re.sub(r"\x1b\[[0-9;]*m", "", result.stdout)
        self.assertIn("Frontend: http://127.0.0.1:22111", output)
        self.assertIn("Backend API: http://127.0.0.1:22112", output)
        self.assertIn("path: /live", manifest)
        self.assertIn("path: /health", manifest)
        self.assertIn("path: /_stcore/health", manifest)
        original = ROOT / "kubernetes" / "manifests" / "dev" / "kustomization.yaml"
        self.assertEqual(
            (
                self.repo / "kubernetes" / "manifests" / "dev" / "kustomization.yaml"
            ).read_text(),
            original.read_text(),
        )

    def test_setup_can_delete_and_recreate_cluster(self):
        result, calls = self.run_script(script="setup_cluster_local", answer="y")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        delete = ["k3d", "cluster", "delete", "devops-app-cluster"]
        create = next(
            call for call in calls if call[:3] == ["k3d", "cluster", "create"]
        )
        self.assertLess(calls.index(delete), calls.index(create))
        self.assertIn("Deleting existing cluster...", result.stdout)
        self.assertIn("Cluster created successfully!", result.stdout)
        self.assertIn("Application deployed successfully!", result.stdout)


if __name__ == "__main__":
    unittest.main()
