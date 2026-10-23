# Local Jenkins controller for afa-pipeline-core: Python for the test stages,
# Docker CLI (talks to the host daemon through the mounted socket) and kubectl.

FROM docker:29.4.0-cli AS docker-cli

FROM jenkins/jenkins:2.580.1-lts-jdk21
ARG KUBECTL_VERSION=v1.34.1
ARG TARGETARCH

USER root
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv curl \
    && rm -rf /var/lib/apt/lists/* \
    && curl -fsSLo /usr/local/bin/kubectl \
       "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
    && chmod +x /usr/local/bin/kubectl
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker

USER jenkins
COPY plugins.txt /usr/share/jenkins/ref/plugins.txt
RUN jenkins-plugin-cli --plugin-file /usr/share/jenkins/ref/plugins.txt
