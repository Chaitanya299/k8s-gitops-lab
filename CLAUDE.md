# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Status

This repository is **empty** — a Kubernetes GitOps repo to be populated. Once
real manifests, tooling, and a directory layout exist, replace this file with
documentation derived from the actual code (build/test/deploy commands and
architecture). Do not invent commands that aren't backed by files in the repo.

## Conventions (apply as the repo is built out)

- This is GitOps: the Git repo is the source of truth. Changes deploy by
  committing manifests, **not** by running `kubectl apply` by hand. Never push
  changes directly to a cluster that bypass Git.
- Keep environment-specific values in overlays, not in base manifests
  (Kustomize `base/` + `overlays/<env>/`, or Helm `values-<env>.yaml`).
- Never commit secrets in plaintext. Use Sealed Secrets, SOPS, or an external
  secrets operator — and verify what the repo adopts before adding any secret.
- Validate manifests before committing (e.g. `kubeconform`, `kustomize build`,
  `helm template`) once the corresponding tooling is in place.

---

## Session Start Protocol ⚡

**MANDATORY** at start of each session:

```bash
# Load essential docs (~800 tokens - 2 min read)
✓ .claude/COMMON_MISTAKES.md      # ⚠️ CRITICAL - Read FIRST
✓ .claude/QUICK_START.md          # Essential commands
✓ .claude/ARCHITECTURE_MAP.md     # File locations
```

**At task completion:**
- Create completion doc in `.claude/completions/YYYY-MM-DD-task-name.md`
- Move session file to `.claude/sessions/archive/` (if created)

**⚠️ NEVER auto-load:**
- Files in `.claude/completions/` (0 token cost)
- Files in `.claude/sessions/` (0 token cost)
- Files in `docs/archive/` (0 token cost)

---

**Last Updated**: 2026-06-29
**Optimized with**: [Claude Token Optimizer](https://github.com/nadimtuhin/claude-token-optimizer)
