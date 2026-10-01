"""Exercise sync guards and rendering without a Docker daemon or cluster."""

import json
import os
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
        print("NAME SERVERS AGENTS LOADBALANCER")
        print(("devops-app-cluster-other" if mode == "missing-cluster" else "devops-app-cluster") + " 1 1 true")
    elif args[:2] == ["image", "import"] and mode == "import":
        sys.exit(1)
elif name == "kubectl":
    if args[:2] != ["--context", "k3d-devops-app-cluster"]:
        sys.exit("Missing explicit context")
    command = args[2:]
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
        for script in ("sync_local", "setup_cluster_local"):
            shutil.copy(ROOT / "kubernetes" / script, manifests.parent / script)
        self.bin = self.directory / "bin"
        self.bin.mkdir()
        for name in ("docker", "k3d", "kubectl"):
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
            "APPLIED_MANIFEST": str(self.applied),
            "REAL_KUBECTL": shutil.which("kubectl"),
            "KUBECONFIG": str(self.directory / "unused-kubeconfig"),
        }

    def run_script(self, mode="", script="sync_local", *args):
        result = subprocess.run(
            ["bash", str(self.repo / "kubernetes" / script), *args],
            env={**self.env, "FAIL_AT": mode},
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
            call for call in calls if call[0] == "kubectl" and call[3] == "apply"
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
                    f"{app}:local-{digest}",
                    "--cluster",
                    "devops-app-cluster",
                    "--mode",
                    "direct",
                ],
                calls,
            )
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


if __name__ == "__main__":
    unittest.main()
