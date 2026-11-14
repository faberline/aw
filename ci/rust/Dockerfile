# The image of the work Jobs of ci-on-gke: ghcr.io/faberline/ci-rust.
# See docs/standards/ci-on-gke.md. It is ci-base for the cluster: a pinned Rust
# toolchain, sccache, kubectl, cargo-llvm-cov, cargo-deny and Node.js, and a user that is not root.
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
ARG CARGO_LLVM_COV_VERSION=0.9.1
ARG CARGO_LLVM_COV_SHA256=3fca950394a3c49457657c158b1619cec8dfd2647ae5b48746734c0ab969a522
ARG CARGO_DENY_VERSION=0.20.2
ARG CARGO_DENY_SHA256=9f12ed4c49936e09b48bf862b595cde2fe64fcbd9d74dfacac6131ca824c8d5f
# Node.js 22: the major version of the ubuntu-latest runner that som and core CI use today.
ARG NODE_VERSION=22.23.4
ARG NODE_SHA256=1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce

ENV RUSTUP_HOME=/usr/local/rustup \
    CARGO_HOME=/usr/local/cargo \
    PATH=/usr/local/cargo/bin:$PATH

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl git build-essential pkg-config libssl-dev cmake clang \
      protobuf-compiler jq zstd xz-utils python3 \
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

# Coverage, dependency audit and Node.js, each pinned by version and checked by SHA-256.
RUN curl --fail --location --silent --show-error --retry 5 --retry-connrefused \
      -o /tmp/llvm-cov.tar.gz \
      "https://github.com/taiki-e/cargo-llvm-cov/releases/download/v${CARGO_LLVM_COV_VERSION}/cargo-llvm-cov-x86_64-unknown-linux-musl.tar.gz" \
 && echo "${CARGO_LLVM_COV_SHA256}  /tmp/llvm-cov.tar.gz" | sha256sum --check --strict \
 && tar -xzf /tmp/llvm-cov.tar.gz -C /usr/local/bin cargo-llvm-cov \
 && curl --fail --location --silent --show-error --retry 5 --retry-connrefused \
      -o /tmp/deny.tar.gz \
      "https://github.com/EmbarkStudios/cargo-deny/releases/download/${CARGO_DENY_VERSION}/cargo-deny-${CARGO_DENY_VERSION}-x86_64-unknown-linux-musl.tar.gz" \
 && echo "${CARGO_DENY_SHA256}  /tmp/deny.tar.gz" | sha256sum --check --strict \
 && tar -xzf /tmp/deny.tar.gz -C /usr/local/bin --strip-components=1 "cargo-deny-${CARGO_DENY_VERSION}-x86_64-unknown-linux-musl/cargo-deny" \
 && curl --fail --location --silent --show-error --retry 5 --retry-connrefused \
      -o /tmp/node.tar.xz \
      "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" \
 && echo "${NODE_SHA256}  /tmp/node.tar.xz" | sha256sum --check --strict \
 && tar -xJf /tmp/node.tar.xz -C /usr/local --strip-components=1 --exclude='*/CHANGELOG.md' --exclude='*/LICENSE' --exclude='*/README.md' \
 && rm /tmp/llvm-cov.tar.gz /tmp/deny.tar.gz /tmp/node.tar.xz

RUN curl --proto '=https' --tlsv1.2 --fail --silent --show-error --retry 5 https://sh.rustup.rs \
      | sh -s -- -y --no-modify-path --profile minimal \
           --default-toolchain "${RUST_VERSION}" --component rustfmt,clippy,llvm-tools-preview \
 && rustc --version && cargo --version && sccache --version && kubectl version --client \
 && cargo llvm-cov --version && cargo deny --version && node --version

# A work Job runs as this user. It writes only under /work (an emptyDir); the
# batch runner sets HOME and CARGO_HOME there. The toolchain stays read-only.
RUN useradd --uid 1000 --user-group --create-home ci
USER 1000:1000
WORKDIR /work

LABEL org.opencontainers.image.source=https://github.com/faberline/aw \
      org.opencontainers.image.description="faberline CI work Job image: pinned Rust, sccache, kubectl, cargo-llvm-cov, cargo-deny, Node.js"
