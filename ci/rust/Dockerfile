# The image of the work Jobs of ci-on-gke: ghcr.io/faberline/ci-rust.
# See docs/standards/ci-on-gke.md. It is ci-base for the cluster: a pinned Rust
# toolchain, sccache and kubectl, and a user that is not root.
# .github/workflows/ci-image.yml builds it every week and on a change here.
#
# It does not start from ghcr.io/faberline/ci-base: that image follows Rust
# stable and holds kind and uv, which a work Job does not use. The tag of this
# image is the Rust version, so a manifest pins the toolchain with the image.

FROM debian:bookworm-slim

ARG RUST_VERSION=1.99.1
ARG SCCACHE_VERSION=v0.19.0
ARG SCCACHE_SHA256=aa73c7270c997894038c0d8f0d297865f57569e1687883322bc431713d4bb9dd
ARG KUBECTL_VERSION=v1.33.1

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git build-essential pkg-config libssl-dev cmake clang \
      protobuf-compiler jq zstd python3 \
 && rm -rf /var/lib/apt/lists/*

RUN curl --fail --location --silent --show-error --retry 5 --retry-connrefused \
      -o /tmp/sccache.tar.gz \
      "https://github.com/mozilla/sccache/releases/download/${SCCACHE_VERSION}/sccache-${SCCACHE_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
 && echo "${SCCACHE_SHA256}  /tmp/sccache.tar.gz" | sha256sum --check --strict \
 && tar -xzf /tmp/sccache.tar.gz -C /tmp \
 && install -m 0755 "/tmp/sccache-${SCCACHE_VERSION}-x86_64-unknown-linux-musl/sccache" /usr/local/bin/sccache \
 && rm -r /tmp/sccache.tar.gz "/tmp/sccache-${SCCACHE_VERSION}-x86_64-unknown-linux-musl" \
 && curl --fail --location --silent --show-error --retry 5 --retry-connrefused \
      -o /usr/local/bin/kubectl \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
 && curl --fail --location --silent --show-error --retry 5 --retry-connrefused \
      -o /tmp/kubectl.sha256 \
      "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl.sha256" \
 && echo "$(cat /tmp/kubectl.sha256)  /usr/local/bin/kubectl" | sha256sum --check --strict \
 && chmod +x /usr/local/bin/kubectl \
 && rm /tmp/kubectl.sha256

RUN curl --proto '=https' --tlsv1.2 --fail --silent --show-error --retry 5 https://sh.rustup.rs \
      | sh -s -- -y --no-modify-path --profile minimal \
           --default-toolchain "${RUST_VERSION}" --component rustfmt,clippy,llvm-tools-preview \
 && rustc --version && cargo --version && sccache --version && kubectl version --client

# A work Job runs as this user. It writes only under /work (an emptyDir); the
# batch runner sets HOME and CARGO_HOME there. The toolchain stays read-only.
RUN useradd --uid 1000 --user-group --create-home ci
USER 1000:1000
WORKDIR /work

LABEL org.opencontainers.image.source=https://github.com/faberline/aw \
      org.opencontainers.image.description="faberline CI work Job image: pinned Rust, sccache, kubectl"
