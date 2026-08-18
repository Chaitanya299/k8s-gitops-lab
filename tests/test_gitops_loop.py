"""Single guard over the core GitOps loop: a Deploy request must mutate the
service's Helm values and land a commit in the source-of-truth repo. If this
breaks, the whole platform is decorative. Runs with plain pytest, no cluster.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ruamel.yaml import YAML

BACKEND = Path(__file__).resolve().parent.parent / "apps" / "platform-backend"
sys.path.insert(0, str(BACKEND))

from app.gitops import DeploySpec, GitOps, apply_settings

yaml = YAML()

INITIAL = """\
replicaCount: 1
autoscaling:
  enabled: true
  minReplicas: 1
  maxReplicas: 5
resources:
  requests:
    cpu: 100m
    memory: 128Mi
  limits:
    cpu: 500m
    memory: 256Mi
env:
  MODEL: echo
"""


def _run(*args: str, cwd: Path) -> None:
    subprocess.run(list(args), cwd=cwd, check=True, capture_output=True, text=True)


def test_apply_settings_is_pure_and_correct() -> None:
    values = yaml.load(INITIAL)
    apply_settings(
        values,
        DeploySpec(service="x", replicas=3, cpu="200m", memory="512Mi", model="gemma"),
    )
    assert values["replicaCount"] == 3
    assert values["autoscaling"]["minReplicas"] == 3  # replicas -> HPA floor
    assert values["resources"]["requests"]["cpu"] == "200m"
    assert values["resources"]["limits"]["memory"] == "512Mi"
    assert values["env"]["MODEL"] == "gemma"


def test_deploy_commits_change(tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    work = tmp_path / "work"

    _run("git", "init", "--bare", "--initial-branch=main", str(origin), cwd=tmp_path)
    _run("git", "clone", str(origin), str(seed), cwd=tmp_path)
    vdir = seed / "gitops" / "environments" / "dev"
    vdir.mkdir(parents=True)
    (vdir / "sample-ai-service.yaml").write_text(INITIAL)
    _run("git", "add", "-A", cwd=seed)
    _run("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-m", "init", cwd=seed)
    _run("git", "push", "origin", "main", cwd=seed)

    gitops = GitOps(repo_url=str(origin), branch="main", workdir=str(work))
    result = gitops.deploy(DeploySpec(service="sample-ai-service", replicas=3, cpu="200m"))
    assert result["committed"] is True

    check = tmp_path / "check"
    _run("git", "clone", str(origin), str(check), cwd=tmp_path)
    landed = yaml.load((check / "gitops/environments/dev/sample-ai-service.yaml").read_text())
    assert landed["replicaCount"] == 3
    assert landed["autoscaling"]["minReplicas"] == 3

    # Re-deploying identical settings must be a no-op (no empty commits).
    again = gitops.deploy(DeploySpec(service="sample-ai-service", replicas=3, cpu="200m"))
    assert again["committed"] is False
