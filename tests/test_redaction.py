"""Redaction is a boundary control: pod logs and git history pass through it on
their way into an LLM request and into durable storage. These tests are the
reason it can be trusted."""
from __future__ import annotations

import pytest
from app.redact import MASK, redact

LEAKS = [
    ("anthropic key", "auth failed for sk-ant-api03-AbCdEf0123456789XyZwVu"),
    ("google key", "GEMINI_API_KEY leaked: AIzaSyA1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q"),
    ("google key v2", "key is AQ.Ab8RN6Jj1kZ2xQwErTyUiOpAsDfGhJkLzXcVbNm0912"),
    ("openai-style key", "using sk-proj-0123456789abcdefghijklmnopqrstuv"),
    ("aws access key", "creds AKIAIOSFODNN7EXAMPLE rejected"),
    ("github token", "remote: ghp_AbCdEf0123456789AbCdEf0123456789"),
    ("slack token", "posting with xoxb-1234567890-abcdefghijkl"),
    (
        "jwt",
        "Cookie: session=eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dBjftJeZ4CVPmB92K27u",
    ),
    ("bearer header", "Authorization: Bearer abcdef0123456789abcdef"),
    ("url credentials", "cloning http://platform:platformdev123@gitea.local/x.git"),
    ("password assignment", "DB_PASSWORD=hunter2correcthorse"),
    ("api key assignment", 'api_key: "abc123def456ghi789"'),
    ("token assignment", "GITEA_TOKEN = 9f8e7d6c5b4a3210"),
]


@pytest.mark.parametrize("label,text", LEAKS, ids=[label for label, _ in LEAKS])
def test_secret_shapes_are_masked(label: str, text: str) -> None:
    out = redact(text)
    assert MASK in out, f"{label} was not redacted: {out}"


@pytest.mark.parametrize("label,text", LEAKS, ids=[label for label, _ in LEAKS])
def test_no_secret_survives_verbatim(label: str, text: str) -> None:
    """The specific secret substring must be gone, not merely accompanied by a mask."""
    out = redact(text)
    secret = text.split()[-1].strip("\"'")
    if len(secret) >= 12:
        assert secret not in out, f"{label} leaked through: {out}"


def test_private_key_block_is_collapsed() -> None:
    pem = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIEowIBAAKCAQEAwJz9M1lqQ0Xn\nabc123\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = redact(f"loaded key:\n{pem}\ndone")
    assert "MIIEowIBAAKCAQEAwJz9M1lqQ0Xn" not in out
    assert MASK in out
    assert out.startswith("loaded key:")


def test_url_credentials_keep_structure() -> None:
    out = redact("git push http://platform:s3cr3tpassword@gitea.local/platform/gitops.git")
    assert "s3cr3tpassword" not in out
    # The host and user survive so the model can still reason about the remote.
    assert "platform:" in out and "@gitea.local" in out


def test_extra_secrets_are_scrubbed_by_exact_match() -> None:
    """Anything the patterns miss still cannot escape if it is a known credential."""
    out = redact("token is zzzz-not-a-known-shape-1234", ("zzzz-not-a-known-shape-1234",))
    assert "zzzz-not-a-known-shape-1234" not in out


def test_short_extra_secrets_cannot_blank_the_text() -> None:
    """A misconfigured 3-char env var must not eat unrelated content."""
    out = redact("the pod is running and healthy", ("the",))
    assert out == "the pod is running and healthy"


NORMAL_LOGS = [
    "INFO  starting sample-ai-service on :8000",
    "GET /generate 200 in 41ms",
    "replicas=3 cpu=200m memory=512Mi",
    "OOMKilled: container exceeded memory limit 256Mi",
    "retries: 3, timeout: 30",
    # Token accounting is everywhere in an AI platform's logs and the assistant
    # has to be able to read it — these are the false positives that matter most.
    "tokens: 1024 processed",
    "max_tokens: 4096",
    "token_count: 5",
    "cache_read_tokens=880",
    'level=error msg="connection refused" attempt=2',
]


@pytest.mark.parametrize("line", NORMAL_LOGS)
def test_ordinary_logs_pass_through_untouched(line: str) -> None:
    """False positives cost the model the context it needs to diagnose anything."""
    assert redact(line) == line


def test_redaction_is_idempotent() -> None:
    once = redact("password=supersecretvalue")
    assert redact(once) == once


def test_empty_input() -> None:
    assert redact("") == ""


def test_placeholder_values_are_left_alone() -> None:
    assert redact("password: null") == "password: null"
    assert redact("token = false") == "token = false"
