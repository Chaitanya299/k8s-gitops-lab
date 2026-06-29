"""The GitOps deploy path: mutate a service's Helm values, commit, push.

This is the ONLY way the platform changes the cluster — it never calls kubectl.
ArgoCD watches the repo and reconciles. `apply_settings` is pure (unit-tested);
`GitOps` wraps it with git I/O against the source-of-truth repo.
"""
from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass, field
from pathlib import Path

from ruamel.yaml import YAML

from .config import settings

_yaml = YAML()
_yaml.preserve_quotes = True
_yaml.indent(mapping=2, sequence=4, offset=2)

# ponytail: one global lock — the git workdir is a single shared checkout, so two
# concurrent deploys must not interleave. Per-service locks only if throughput matters.
_LOCK = threading.Lock()


@dataclass
class DeploySpec:
    service: str
    replicas: int = 1
    cpu: str = "100m"
    memory: str = "128Mi"
    namespace: str = "ai-services"
    image_tag: str | None = None
    model: str | None = None
    env: dict[str, str] = field(default_factory=dict)


def apply_settings(values: dict, spec: DeploySpec) -> dict:
    """Mutate a Helm values mapping in place from a deploy request. Pure / testable."""
    values["replicaCount"] = spec.replicas

    auto = values.setdefault("autoscaling", {})
    if auto.get("enabled", True):
        # Map the dashboard's replica count to HPA minReplicas so it's always running.
        auto["minReplicas"] = spec.replicas
        auto["maxReplicas"] = max(spec.replicas, int(auto.get("maxReplicas", spec.replicas)))

    res = values.setdefault("resources", {})
    req = res.setdefault("requests", {})
    lim = res.setdefault("limits", {})
    req["cpu"], lim["cpu"] = spec.cpu, spec.cpu
    req["memory"], lim["memory"] = spec.memory, spec.memory

    if spec.image_tag:
        values.setdefault("image", {})["tag"] = spec.image_tag

    env = values.setdefault("env", {})
    if spec.model:
        env["MODEL"] = spec.model
    # ponytail: v1 deploys to the ArgoCD app's fixed target namespace. Record the
    # requested namespace for visibility; real multi-namespace needs a generated
    # Application per namespace — deferred.
    env["NAMESPACE"] = spec.namespace
    for k, v in spec.env.items():
        env[k] = v
    return values


class GitOps:
    def __init__(
        self, repo_url: str | None = None, branch: str | None = None, workdir: str | None = None
    ) -> None:
        self.repo_url = repo_url or settings.gitops_repo_url
        self.branch = branch or settings.gitops_branch
        self.workdir = Path(workdir or settings.workdir)

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args], cwd=self.workdir, check=True, capture_output=True, text=True
        )

    def _ensure_clone(self) -> None:
        if (self.workdir / ".git").exists():
            self._git("fetch", "origin", self.branch)
            self._git("reset", "--hard", f"origin/{self.branch}")
            self._git("clean", "-fd")
        else:
            self.workdir.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["git", "clone", "--branch", self.branch, self.repo_url, str(self.workdir)],
                check=True,
                capture_output=True,
                text=True,
            )
        self._git("config", "user.name", settings.git_author_name)
        self._git("config", "user.email", settings.git_author_email)

    def deploy(self, spec: DeploySpec) -> dict:
        with _LOCK:
            self._ensure_clone()
            rel = settings.values_template.format(service=spec.service)
            path = self.workdir / rel
            if not path.exists():
                raise FileNotFoundError(f"no values file for service '{spec.service}' ({rel})")

            with path.open() as f:
                values = _yaml.load(f)
            apply_settings(values, spec)
            with path.open("w") as f:
                _yaml.dump(values, f)

            self._git("add", rel)
            staged = subprocess.run(
                ["git", "diff", "--cached", "--quiet"], cwd=self.workdir
            )
            if staged.returncode == 0:
                return {"service": spec.service, "committed": False, "message": "no change"}

            msg = (
                f"deploy {spec.service}: replicas={spec.replicas} "
                f"cpu={spec.cpu} mem={spec.memory}"
            )
            self._git("commit", "-m", msg)
            self._git("push", "origin", self.branch)
            sha = self._git("rev-parse", "HEAD").stdout.strip()
            return {"service": spec.service, "committed": True, "commit": sha[:8], "message": msg}

    def history(self, service: str | None = None, limit: int = 20) -> list[dict]:
        with _LOCK:
            self._ensure_clone()
            fmt = "%H%x1f%an%x1f%aI%x1f%s"
            args = ["log", f"--max-count={limit}", f"--pretty=format:{fmt}", "--"]
            args.append(
                settings.values_template.format(service=service)
                if service
                else "gitops/environments/dev"
            )
            out = self._git(*args).stdout.strip()
        commits = []
        for line in filter(None, out.splitlines()):
            h, author, date, subject = line.split("\x1f")
            commits.append(
                {"commit": h[:8], "author": author, "date": date, "message": subject}
            )
        return commits
