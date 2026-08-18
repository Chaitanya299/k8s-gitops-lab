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

    # ── Chat assistant ──────────────────────────────────────────────────────
    # Unset is a valid, loud state: the chat routes 503 with a setup message
    # rather than silently picking a provider.
    llm_provider: str = os.getenv("LLM_PROVIDER", "")
    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "")
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-opus-5")
    llm_effort: str = os.getenv("LLM_EFFORT", "high")
    # Caps thinking + response text together, so it needs real headroom.
    llm_max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "16000"))
    ollama_url: str = os.getenv("OLLAMA_URL", "http://ollama.platform.svc.cluster.local:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.1")
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    gemini_base_url: str = os.getenv(
        "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com"
    )

    knowledge_db_path: str = os.getenv("KNOWLEDGE_DB_PATH", "/data/knowledge.db")
    chat_token_budget: int = int(os.getenv("CHAT_TOKEN_BUDGET", "200000"))
    chat_rate_limit_per_min: int = int(os.getenv("CHAT_RATE_LIMIT_PER_MIN", "20"))
    chat_retention_days: int = int(os.getenv("CHAT_RETENTION_DAYS", "30"))
    chat_max_iterations: int = int(os.getenv("CHAT_MAX_ITERATIONS", "12"))


settings = Settings()
