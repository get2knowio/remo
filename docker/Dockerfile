# docker/Dockerfile
#
# Multi-stage image for `remo web` (010-web-session-interface).
#
# Stage 1 (frontend-builder): builds the TypeScript SPA (frontend/) with Node.
# Stage 2 (wheel-builder):    builds a `remo-cli` wheel with hatchling (no
#                              build tools ship in the runtime layer).
# Stage 3 (runtime):          slim Python runtime that serves the API + the
#                              built SPA assets, plus the SSH/AWS SSM tooling
#                              needed to reach session targets.
#
# See specs/010-web-session-interface/research.md R8.

# ---------------------------------------------------------------------------
# Stage 1: frontend-builder
# ---------------------------------------------------------------------------
FROM node:20-slim AS frontend-builder

WORKDIR /app/frontend

COPY frontend/ .

# Build output directory matches `frontend/dist` (Vite's default
# `build.outDir`, see frontend/vite.config.ts) and is a static,
# same-origin-servable SPA.
RUN npm ci && npm run build

# ---------------------------------------------------------------------------
# Stage 2: wheel-builder
# ---------------------------------------------------------------------------
# Builds a wheel for remo-cli[web] so the runtime stage doesn't need build
# tools (hatchling) installed — mirrors the repo-root Dockerfile's approach.
FROM python:3.11-slim AS wheel-builder

WORKDIR /build

RUN pip install --no-cache-dir hatchling

COPY pyproject.toml README.md ./
COPY src/ src/
COPY ansible/ ansible/

RUN pip wheel --no-deps --wheel-dir /build/wheels .

# ---------------------------------------------------------------------------
# Stage 3: runtime
# ---------------------------------------------------------------------------
FROM python:3.11-slim AS runtime

# Enables arch-specific package selection below (docker buildx sets this
# automatically for multi-arch builds; amd64/arm64 per SC-006).
ARG TARGETARCH

LABEL org.opencontainers.image.description="remo web session interface"

WORKDIR /app

# Install OS-level runtime dependencies:
#   openssh-client - ssh, ssh-keygen, ControlMaster sockets
#   curl           - AWS CLI/SSM plugin install below, and the container
#                    healthcheck (compose.example.yml) at runtime
#   unzip          - AWS CLI v2 install below
#   gosu           - drops from root to the unprivileged `remo` user in the
#                    entrypoint AFTER self-healing filesystem permissions
#                    (see docker/entrypoint.sh). Unlike `su`, it stays PID 1
#                    with correct signal semantics and adds no setuid-binary
#                    surface. Packaged for both amd64 and arm64 in Debian, so
#                    the multi-arch build is unaffected.
RUN apt-get update && \
    apt-get install -y --no-install-recommends openssh-client curl unzip gosu && \
    rm -rf /var/lib/apt/lists/*

# Install AWS CLI v2 (needed for SSM proxy), arch-selected via $TARGETARCH.
# TARGETARCH is set by Docker buildx to the actual build TARGET arch
# (amd64/arm64/...), unlike `dpkg --print-architecture` which reports the
# BUILD host's arch and would be wrong when cross-compiling (FR-027/FR-042).
RUN if [ "$TARGETARCH" = "arm64" ]; then AWS_ARCH="aarch64"; else AWS_ARCH="x86_64"; fi && \
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}.zip" -o /tmp/awscliv2.zip && \
    unzip -q /tmp/awscliv2.zip -d /tmp && \
    /tmp/aws/install && \
    rm -rf /tmp/aws /tmp/awscliv2.zip

# Install session-manager-plugin (needed for SSM tunnels), same $TARGETARCH
# arch-selection approach.
RUN if [ "$TARGETARCH" = "arm64" ]; then SM_ARCH="arm64"; else SM_ARCH="64bit"; fi && \
    curl -fsSL "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_${SM_ARCH}/session-manager-plugin.deb" \
        -o /tmp/session-manager-plugin.deb && \
    dpkg -i /tmp/session-manager-plugin.deb && \
    rm /tmp/session-manager-plugin.deb

# Non-root user (UID/GID 1000 to match compose.example.yml `user: "1000:1000"`).
RUN useradd -u 1000 -m -s /bin/bash remo

# Install remo-cli[web] system-wide (as root) from the wheel built in stage 2
# — avoids needing build tools in this final layer and avoids `pip install
# --user` (so no PATH fix-up is needed; `remo` / `remo web` land directly on
# the system PATH for the `remo` user the entrypoint drops to).
COPY --from=wheel-builder /build/wheels/*.whl /tmp/
RUN pip install --no-cache-dir "$(ls /tmp/*.whl)[web]" && \
    rm /tmp/*.whl

# Built frontend assets, placed where REMO_WEB_FRONTEND_DIST_DIR (below)
# points the running service at.
COPY --from=frontend-builder --chown=remo:remo /app/frontend/dist ./frontend-dist

COPY --chown=remo:remo docker/entrypoint.sh ./docker/entrypoint.sh
RUN chmod +x ./docker/entrypoint.sh && chown remo:remo /app

# `WebSettings.frontend_dist_dir` defaults (absent this override) to
# `<repo_root>/frontend/dist` resolved relative to the INSTALLED PACKAGE
# location (see src/remo_cli/web/config.py `_default_frontend_dist_dir()`),
# which does not exist inside this image — the built assets live at
# /app/frontend-dist instead (COPY above). Point the service at the real
# path via the override mechanism that function documents.
ENV REMO_WEB_FRONTEND_DIST_DIR=/app/frontend-dist

# WebSettings.bind_host defaults to 127.0.0.1 (safe for bare `remo web
# serve` on a dev machine), but inside a container that means Docker's port
# publishing (compose.example.yml `ports:`) can never reach the process —
# packets DNAT'd to the container's own interface never hit a socket bound
# only to loopback. Bind widely inside the container's own network
# namespace; the LAN-exposure decision stays with the host-side bind
# address in compose.example.yml's `ports:` mapping (127.0.0.1-only by
# default, FR-052), same as any other containerized service.
ENV REMO_WEB_BIND_HOST=0.0.0.0

# Pin HOME to the `remo` user's home so config-path resolution
# (core/config.py `Path.home()` / REMO_HOME) is identical whether the
# entrypoint is running its root-side healing pass or the dropped-to `remo`
# process — otherwise root's ambient HOME (/root) would resolve the config
# dir to a different path than the app later reads.
ENV HOME=/home/remo

# NOTE: intentionally NO `USER remo` here. The image starts as root so the
# entrypoint can self-heal ownership on a bind-mounted, root-owned config dir
# and on the /run/remo-ssh tmpfs (which a container *restart* remounts
# root-owned) BEFORE dropping — via gosu — to the unprivileged `remo` user.
# The app still ends up running as non-root `remo`; the drop just happens at
# runtime in the entrypoint instead of at image-build time. A deployer that
# sets an explicit `--user`/`user:` starts non-root, and the entrypoint skips
# healing (best-effort) and execs directly. See docker/entrypoint.sh.
ENTRYPOINT ["/app/docker/entrypoint.sh"]
