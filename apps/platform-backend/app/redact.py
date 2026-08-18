"""Strip secrets out of anything on its way into a prompt or the knowledge store.

This is a boundary control, not a convenience. Pod logs and git history are
workload-controlled text that ends up in an LLM request and in durable storage,
so redaction happens once, here, at the edge — a leak should require this
function to be wrong, not a caller to have forgotten.

Pure and side-effect free so it can be exhaustively tested (tests/test_redaction.py).
"""
from __future__ import annotations

import re

MASK = "[REDACTED]"

# Values that look like an assignment but carry no secret. Redacting these adds
# noise to the model's context without protecting anything.
_PLACEHOLDERS = frozenset(
    {"null", "none", "nil", "true", "false", "yes", "no", "", "-", MASK.lower()}
)

# Ordered: multi-line block patterns first, then structured formats, then the
# generic key=value sweep that would otherwise chew into the more specific ones.
_PATTERNS: list[tuple[str, re.Pattern[str], str]] = [
    (
        "private_key",
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
        ),
        f"-----BEGIN PRIVATE KEY-----{MASK}-----END PRIVATE KEY-----",
    ),
    # http://user:password@host — how this platform's own Gitea credentials travel.
    (
        "url_credentials",
        re.compile(r"(?P<scheme>[a-zA-Z][a-zA-Z0-9+.\-]*://)(?P<user>[^:/\s@]+):[^@/\s]+@"),
        rf"\g<scheme>\g<user>:{MASK}@",
    ),
    ("anthropic_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,}"), MASK),
    ("google_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b"), MASK),
    # Newer Google/Gemini key format: starts AQ. and carries dot-separated segments.
    ("google_key_v2", re.compile(r"\bAQ\.[A-Za-z0-9_\-]{4,}(?:\.[A-Za-z0-9_\-]+)*"), MASK),
    ("api_key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"), MASK),
    ("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"), MASK),
    ("slack_token", re.compile(r"\bxox[abprs]-[A-Za-z0-9\-]{10,}"), MASK),
    (
        "aws_access_key",
        re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA)[A-Z0-9]{16}\b"),
        MASK,
    ),
    (
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),
        MASK,
    ),
    (
        "auth_header",
        re.compile(r"(?i)\b(?P<scheme>bearer|basic)\s+(?P<val>[A-Za-z0-9._\-+/=]{12,})"),
        rf"\g<scheme> {MASK}",
    ),
]

# password=…, api_key: …, SECRET_TOKEN="…". Keeps the key so the model can still
# reason about what was configured, drops the value.
#
# The suffix after the secret word may only continue across a separator
# (`_`, `-`, `.`), never a bare letter. Without that, "token" matches inside
# "tokens" and every token-count line in an AI platform's logs gets mangled.
_ASSIGNMENT = re.compile(
    r"(?i)(?P<key>\b[A-Za-z0-9_.\-]*"
    r"(?:passwd|password|secret|token|api[_\-]?key|access[_\-]?key|"
    r"private[_\-]?key|credential|authorization)"
    r"(?:[_.\-][A-Za-z0-9_.\-]*)?\b)"
    r"(?P<sep>\s*[:=]\s*)"
    r"(?P<quote>[\"']?)(?P<val>[^\s\"',;)}\]]+)(?P=quote)"
)

_NUMERIC = re.compile(r"^\d+$")


def _mask_assignment(m: re.Match[str]) -> str:
    value = m.group("val").strip()
    lowered = value.lower()
    if lowered in _PLACEHOLDERS or lowered.startswith("[redacted"):
        return m.group(0)
    # Counters and limits (`token_count: 5`, `max_tokens: 4096`) are the common
    # case for a numeric value under a secret-shaped key, and the assistant needs
    # to read them. A real credential is not sixteen-odd bare digits.
    if _NUMERIC.match(value) and len(value) < 16:
        return m.group(0)
    q = m.group("quote")
    return f"{m.group('key')}{m.group('sep')}{q}{MASK}{q}"


def redact(text: str, extra_secrets: tuple[str, ...] = ()) -> str:
    """Return `text` with credential-shaped substrings masked.

    `extra_secrets` are exact values to scrub regardless of shape — pass the
    platform's own known credentials so a format the patterns miss still can't
    escape. Idempotent: redacting already-redacted text is a no-op.
    """
    if not text:
        return text

    for value in extra_secrets:
        # Guard against a short/empty env var blanking out unrelated text.
        if value and len(value) >= 8:
            text = text.replace(value, MASK)

    for _name, pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)

    return _ASSIGNMENT.sub(_mask_assignment, text)


def platform_secrets() -> tuple[str, ...]:
    """This platform's own credentials, for exact-match scrubbing.

    Read at call time rather than import time so tests can set env vars first.
    """
    from .config import settings

    candidates = [settings.anthropic_api_key, settings.gemini_api_key]
    # The Gitea password rides inside the repo URL's userinfo.
    m = re.match(r"[a-zA-Z][a-zA-Z0-9+.\-]*://[^:/\s@]+:([^@/\s]+)@", settings.gitops_repo_url)
    if m:
        candidates.append(m.group(1))
    return tuple(c for c in candidates if c)
