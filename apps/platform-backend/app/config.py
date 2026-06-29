"""Runtime config, all from env (12-factor). No secrets in code."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    gitops_repo_url: str = os.getenv(
        "GITOPS_REPO_URL",
        "http://gitea-http.gitea.svc.cluster.local:3000/platform/gitops.git",
    )
    gitops_branch: str = os.getenv("GITOPS_BRANCH", "main")
    values_template: str = os.getenv(
        "GITOPS_VALUES_TEMPLATE", "gitops/environments/dev/{service}.yaml"
    )
    workdir: str = os.getenv("GITOPS_WORKDIR", "/tmp/gitops")
    ai_namespace: str = os.getenv("AI_NAMESPACE", "ai-services")
    prometheus_url: str = os.getenv(
        "PROMETHEUS_URL",
        "http://kube-prometheus-stack-prometheus.monitoring.svc.cluster.local:9090",
    )
    git_author_name: str = os.getenv("GIT_AUTHOR_NAME", "platform-bot")
    git_author_email: str = os.getenv("GIT_AUTHOR_EMAIL", "platform-bot@local")


settings = Settings()
