# Platform overview

This is the assistant's seed knowledge about how the platform works. It is
indexed at startup so the assistant can answer "how does X work here" from the
platform's own rules rather than generic Kubernetes lore.

## Deploys go through git, never kubectl

A deploy is a git commit. When settings change, the control plane edits the
service's Helm values file in a Gitea repository, commits, and pushes. ArgoCD
watches that repository and reconciles the cluster to match the commit. The
backend has read-only Kubernetes access and cannot write to the cluster
directly. There is no path that runs `kubectl apply`.

## How fast a deploy lands

ArgoCD polls the Gitea repository every 30 seconds, so a commit typically
reconciles into the cluster within about a minute of the Deploy action. Use the
ArgoCD sync status to confirm a specific commit has landed.

## Replicas and autoscaling

The dashboard's replicas value maps to `autoscaling.minReplicas` in the Helm
values, so the requested number is always running. Autoscaling is enabled by
default with an HPA; if a requested replica count does not appear, the HPA may
be scaling on CPU. `maxReplicas` is clamped to be at least the requested count.

## Resources

Deploy requests set CPU and memory requests (and limits, equal to requests) on
the service. A pod that shows OOMKilled or CrashLoopBackOff after a deploy has
usually hit its memory limit — raising the memory request/limit is the typical
fix. Valid Kubernetes quantity strings are required, e.g. `200m` for CPU and
`512Mi` for memory.

## Namespaces

Deployed AI services run in the `ai-services` namespace. The platform's own
frontend and backend run in the `platform` namespace. Monitoring runs in
`monitoring`, Gitea in `gitea`, and ArgoCD in `argocd`.

## The sample service

The sample AI service echoes the prompt back by default. It only proxies a real
model when the `OLLAMA_URL` environment variable is set on the service. The
model name is passed through the `MODEL` environment variable in its values.

## Metrics

Prometheus scrapes the sample service's `/metrics` endpoint for request rate,
p95 latency, error rate, and in-flight request count. Grafana charts these on
the AI platform dashboard. Use current metrics to size replicas from real
traffic rather than guessing.

## Deployment history

Deployment history is the git log of the gitops repository. Each deploy is a
commit, so there is no separate database recording who deployed what — the
history and the source of truth are the same thing.

## Hostnames and images

Services are reachable via nip.io hostnames such as
`sample-ai-service.127.0.0.1.nip.io`, so no `/etc/hosts` edits are needed.
Container images are built and pushed to the local registry at `localhost:5001`
and referenced from the Helm values as `localhost:5001/<service>`.
