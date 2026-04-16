FROM python:3.13-slim AS builder

WORKDIR /build

# Install build dependencies
RUN pip install --no-cache-dir hatchling

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ src/

# Build the wheel
RUN pip wheel --no-deps --wheel-dir /build/wheels .

# -------------------------------------------------------------------
FROM python:3.13-slim

LABEL org.opencontainers.image.source="https://github.com/get2knowio/remo"
LABEL org.opencontainers.image.description="Remote development environment CLI"

# Install runtime OS dependencies:
#   openssh-client  - ssh, ssh-keygen
#   rsync           - remo cp
#   less            - ansible pager
#   git             - ansible galaxy may need it
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        openssh-client \
        rsync \
        less \
        git \
        curl \
        unzip \
    && rm -rf /var/lib/apt/lists/*

# Install AWS CLI v2 (needed for SSM proxy)
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ]; then AWS_ARCH="aarch64"; else AWS_ARCH="x86_64"; fi && \
    curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-${AWS_ARCH}.zip" -o /tmp/awscliv2.zip && \
    unzip -q /tmp/awscliv2.zip -d /tmp && \
    /tmp/aws/install && \
    rm -rf /tmp/aws /tmp/awscliv2.zip

# Install session-manager-plugin (needed for SSM tunnels)
RUN ARCH=$(dpkg --print-architecture) && \
    if [ "$ARCH" = "arm64" ]; then SM_ARCH="arm64"; else SM_ARCH="64bit"; fi && \
    curl -fsSL "https://s3.amazonaws.com/session-manager-downloads/plugin/latest/ubuntu_${SM_ARCH}/session-manager-plugin.deb" \
        -o /tmp/session-manager-plugin.deb && \
    dpkg -i /tmp/session-manager-plugin.deb && \
    rm /tmp/session-manager-plugin.deb

# Create non-root user
RUN useradd -m -s /bin/bash remo
USER remo
WORKDIR /home/remo

# Install the wheel and its dependencies
# The wheel includes ansible/ playbooks bundled as remo_cli/ansible/
COPY --from=builder --chown=remo:remo /build/wheels/*.whl /tmp/
RUN pip install --no-cache-dir --user /tmp/*.whl && \
    rm /tmp/*.whl

# Ensure user bin is on PATH
ENV PATH="/home/remo/.local/bin:${PATH}"

# Pre-install Ansible Galaxy collections into the image so they don't need
# to be fetched on first run.
RUN ansible-galaxy collection install \
    -r "$(python3 -c 'from remo_cli.core.config import get_ansible_dir; print(get_ansible_dir() / "requirements.yml")')"

ENTRYPOINT ["remo"]
