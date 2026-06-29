"""Auth seam.

ponytail: v1 has no auth (deferred per scope). This dependency is where JWT +
Admin/Developer/Viewer roles slot in later — make it parse/verify a token and
raise 401/403. Every protected route already depends on require_user(), so
turning auth on is a one-file change.
"""
from __future__ import annotations


def require_user() -> dict[str, str]:
    return {"sub": "dev", "role": "admin"}
