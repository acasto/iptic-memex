FROM ubuntu:24.04

# Install essential build tools and commonly needed packages
RUN apt-get update && apt-get install -y \
    # Python and packages
    python3 \
    python3-pip \
    python3-venv \
    python3-requests \
    python3-pandas \
    python3-numpy \
    python3-matplotlib \
    python3-pytest \
    python3-pytest-cov \
    python3-yaml \
    # Network tools
    dnsutils \
    curl \
    wget \
    netcat-openbsd \
    net-tools \
    iputils-ping \
    iproute2 \
    # Development tools
    git \
    jq \
    xmlstarlet \
    vim \
    miller \
    nodejs \
    npm \
    build-essential \
    # System tools
    htop \
    tree \
    lsof \
    # Text processing
    ripgrep \
    fd-find \
    moreutils \
    bat \
    patch \
    # SSL/TLS tools
    openssl \
    ca-certificates \
    # Compression tools
    zip \
    unzip \
    tar \
    gzip \
    # Shell utilities
    tmux \
    less \
    # Additional languages
    sqlite3 \
    # Cleanup
    && rm -rf /var/lib/apt/lists/* \
    && ln -s /usr/bin/fdfind /usr/bin/fd

# Install Python-based tools
RUN pip3 install --no-cache-dir --break-system-packages semgrep jsonpatch ruff

# Install ast-grep
RUN npm install -g @ast-grep/cli

# Static binaries, per-arch (defaults to arm64; set TARGETARCH for multi-arch builds)
ARG TARGETARCH

# Modern sed alternative (sd names assets per-arch)
RUN case "${TARGETARCH:-arm64}" in \
      arm64) SD_ASSET=sd-v1.1.0-aarch64-unknown-linux-musl.tar.gz ;; \
      amd64) SD_ASSET=sd-v1.1.0-x86_64-unknown-linux-musl.tar.gz ;; \
    esac && \
    curl -L "https://github.com/chmln/sd/releases/download/v1.1.0/${SD_ASSET}" | tar xz -C /usr/local/bin --strip-components=1

# YAML processing (yq) and dasel (TOML/CSV/XML), both per-arch
RUN wget -qO /usr/local/bin/yq "https://github.com/mikefarah/yq/releases/latest/download/yq_linux_${TARGETARCH:-arm64}" && chmod +x /usr/local/bin/yq && \
    curl -L -o /usr/local/bin/dasel "https://github.com/TomWright/dasel/releases/download/v3.11.2/dasel_linux_${TARGETARCH:-arm64}" && chmod +x /usr/local/bin/dasel

# Set working directory
WORKDIR /workspace

# Use a non-root user for better security
RUN useradd -m -s /bin/bash sandbox
USER sandbox

# Set up a nicer environment
ENV LANG=C.UTF-8
ENV TERM=xterm-256color

CMD ["/bin/bash"]
