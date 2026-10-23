#!/usr/bin/env bash
# Start the local Jenkins next to a running Minikube.
set -euo pipefail
cd "$(dirname "$0")"

if ! minikube status >/dev/null 2>&1; then
  echo "Minikube is not running; starting it" >&2
  minikube start --driver=docker --cpus=2 --memory=2g
fi

# Kubeconfig for the Jenkins container: certificates inlined, API server
# addressed by the Minikube container name on the shared Docker network.
kubectl config view --minify --flatten --context minikube \
  | sed -E 's#server: https://[^ ]+#server: https://minikube:8443#' > kubeconfig
chmod 644 kubeconfig

if [ ! -f .env ]; then
  printf 'JENKINS_ADMIN_ID=admin\nJENKINS_ADMIN_PASSWORD=%s\n' "$(openssl rand -hex 12)" > .env
  echo "Created jenkins/.env with a random admin password" >&2
fi

docker compose up -d --build
echo "Jenkins: http://localhost:8080 (credentials in jenkins/.env)"
