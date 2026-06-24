# Remo Notifier — multi-stage build.
#
# All COPY paths are relative to the build-context root. The context root is
# either the repo root (local builds: `docker build -f notifier/Dockerfile .`)
# or the on-host copy of the bundled context (remo_notifier Ansible role), which
# reproduces the same layout. See specs/007-notifier-sidecar (finding U1).

# ---- Stage 1: builder ------------------------------------------------------
FROM python:3.13-slim AS builder
# Channel selector: builds a per-channel image installing only that channel's
# extra (spec 008, research R4). `remo notifier deploy` passes --build-arg
# CHANNEL=<id>; the produced image is tagged remo-notifier-<channel>:<version>.
ARG CHANNEL=telegram
WORKDIR /build
RUN pip install --no-cache-dir uv
# pyproject.toml + README.md + source are required to install the project.
# uv.lock is included for an optional `uv sync --frozen` locked build; a plain
# `uv pip install` does not consume it (finding I1).
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/
# The remo-cli wheel force-includes ansible/ (and the Dockerfile lives under
# notifier/), so both must be present in the build context for hatchling to
# resolve its forced includes during install.
COPY ansible/ ./ansible/
COPY notifier/ ./notifier/
RUN uv venv /opt/venv && \
    . /opt/venv/bin/activate && \
    uv pip install --no-cache-dir ".[notifier-${CHANNEL}]"

# ---- Stage 2: runtime ------------------------------------------------------
FROM python:3.13-slim
RUN groupadd --system --gid 65532 notifier && \
    useradd --system --uid 65532 --gid notifier --no-create-home \
        --shell /usr/sbin/nologin notifier
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
USER 65532:65532
EXPOSE 18181
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:18181/v1/health', timeout=2)"
ENTRYPOINT ["remo-notifier"]
CMD ["serve", "--config", "/etc/notifier/notifier.toml"]
