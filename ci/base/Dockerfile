# The shared CI base image: ghcr.io/faberline/ci-base.
# It holds the tools that faberline CI otherwise downloads in every job.
# .github/workflows/ci-image.yml builds it every week and on a change here.

FROM ghcr.io/astral-sh/uv:0.13.7 AS uv

FROM debian:bookworm-slim

ARG KIND_VERSION=v0.29.0
ARG KIND_SHA256=c72eda46430f065fb45c5f70e7c957cc9209402ef309294821978677c8fb3284
ARG KUBECTL_VERSION=v1.33.1

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git build-essential pkg-config libssl-dev python3 python3-venv \
 && rm -rf /var/lib/apt/lists/*

RUN curl --fail --location --silent --show-error --retry 5 --retry-connrefused \
      -o /usr/local/bin/kind \
      "https://github.com/kubernetes-sigs/kind/releases/download/${KIND_VERSION}/kind-linux-amd64" \
 && echo "${KIND_SHA256}  /usr/local/bin/kind" | sha256sum --check --strict \
 && chmod +x /usr/local/bin/kind \
 && curl --fail --location --silent --show-error --retry 5 --retry-connrefused \
      -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && curl --fail --location --silent --show-error --retry 5 --retry-connrefused \
      -o /tmp/kubectl.sha256 \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" \
 && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum --check --strict \
 && chmod +x /usr/local/bin/kubectl \
 && rm /tmp/kubectl.sha256

COPY --from=uv /uv /uvx /usr/local/bin/

RUN curl --proto '=https' --tlsv1.2 --fail --silent --show-error --retry 5 https://sh.rustup.rs \
      | sh -s -- -y --no-modify-path --profile minimal \
           --default-toolchain stable --component rustfmt,clippy \
 && chmod -R a+w "$RUSTUP_HOME" "$CARGO_HOME" \
 && rustc --version && cargo --version && uv --version && kind version && kubectl version --client

LABEL org.opencontainers.image.source=https://github.com/faberline/aw \
      org.opencontainers.image.description="faberline CI base: Rust stable, uv, kind, kubectl"
