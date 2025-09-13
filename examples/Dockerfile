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
RUN pip3 install --no-cache-dir --break-system-packages semgrep jsonpatch

# Install ast-grep
RUN npm install -g @ast-grep/cli

# Modern sed alternative
RUN curl -L https://github.com/chmln/sd/releases/latest/download/sd-v1.0.0-aarch64-unknown-linux-musl.tar.gz | tar xz -C /usr/local/bin --strip-components=1

# YAML processing
RUN wget -qO /usr/local/bin/yq https://github.com/mikefarah/yq/releases/latest/download/yq_linux_amd64 && chmod +x /usr/local/bin/yq

RUN curl -L -o /usr/local/bin/comby https://github.com/comby-tools/comby/releases/download/1.8.1/comby-1.8.1-x86_64-linux && \
    chmod +x /usr/local/bin/comby

# Set working directory
WORKDIR /workspace

# Use a non-root user for better security
RUN useradd -m -s /bin/bash sandbox
USER sandbox

# Set up a nicer environment
ENV LANG=C.UTF-8
ENV TERM=xterm-256color

CMD ["/bin/bash"]
