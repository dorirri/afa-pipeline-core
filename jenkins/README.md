# Local Jenkins + Minikube

A self-contained CI/CD stand for this repository: Jenkins runs in Docker, builds the
project image with the host Docker daemon and deploys it to Minikube as a Kubernetes Job.
The pipeline itself is in [`../Jenkinsfile`](../Jenkinsfile).

```
GitHub ──push──▶ webhook (ngrok) ──▶ Jenkins ──▶ lint · mypy · pytest
                                           │
                                           ├─▶ docker build ─▶ smoke test
                                           └─▶ docker save | ctr import (Minikube node) ─▶ kubectl apply k8s/job.yaml
```

## Requirements

Docker Desktop (≥ 4 GB memory), `minikube`, `kubectl`, and optionally `ngrok`.

## Start

```bash
./jenkins/up.sh
```

The script starts Minikube if needed, writes `jenkins/kubeconfig` (API server at
`https://minikube:8443` on the shared `minikube` Docker network), creates `jenkins/.env`
with a random admin password, and runs `docker compose up -d --build`.

Open <http://localhost:8080> and log in with the credentials from `jenkins/.env`.
Plugins, security and the `afa-pipeline-core` job are created by
[Configuration as Code](casc/jenkins.yaml), so there is no setup wizard.

## Trigger on every commit

The job has two triggers:

- **GitHub webhook** (instant). Expose Jenkins with `ngrok http 8080`, then in the
  repository go to *Settings → Webhooks → Add webhook*:
  - Payload URL: `https://<ngrok-domain>/github-webhook/`
  - Content type: `application/json`
  - Events: *Just the push event*
- **Poll SCM** every 5 minutes, as a fallback when the tunnel is down.

The free ngrok plan gives a new domain on every restart unless you claim the free
static domain in the ngrok dashboard (`ngrok http --url=<domain> 8080`).

## Pipeline stages

| Stage | What it does |
|---|---|
| Set up Python | venv + `pip install -e ".[dev]"` |
| Lint | `ruff check`, `ruff format --check` |
| Type check | `mypy` (strict) |
| Unit tests | `pytest` with JUnit report and coverage (fails under 85 %) |
| Build Docker image | `afa-pipeline-core:<build>-<commit>` and `:latest` |
| Smoke test image | `--version` and `analyzers` with `--network none` |
| Deploy to Minikube | loads the image into the node, runs [`k8s/job.yaml`](../k8s/job.yaml) and waits for it to complete |

## Simplifications

This is a local, single-user stand: builds run on the built-in node, and the Docker
socket is mounted into the controller (`group_add: ["0"]`), which gives Jenkins full
control of the host Docker daemon. A shared server would use separate agents instead.

## Stop

```bash
docker compose -f jenkins/docker-compose.yml down      # keep Jenkins data
docker compose -f jenkins/docker-compose.yml down -v   # also delete it
minikube stop
```
