# Build only the provider-free Python distribution. CLI agents and credentials
# are deliberately absent; the image supports MCP startup and local tools.
FROM python:3.12-slim AS builder
WORKDIR /build
COPY pyproject.toml requirements.txt README.md LICENSE MANIFEST.in ./
COPY server.py config.py ./
COPY clink/ clink/
COPY tools/ tools/
COPY utils/ utils/
COPY systemprompts/ systemprompts/
COPY conf/__init__.py conf/__init__.py
COPY conf/cli_clients/ conf/cli_clients/
RUN python -m pip wheel --no-cache-dir --wheel-dir /wheels .

FROM python:3.12-slim AS runtime
LABEL org.opencontainers.image.title="clink-mcp-server" \
      org.opencontainers.image.description="CLI orchestration MCP server; discovery and local tools only in the base image" \
      org.opencontainers.image.version="0.2.3" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.source="https://github.com/acebytes/clink-cli"
RUN groupadd --gid 10001 clink && \
    useradd --uid 10001 --gid clink --create-home --shell /usr/sbin/nologin clink
COPY --from=builder /wheels /wheels
RUN python -m pip install --no-cache-dir --no-index --find-links /wheels clink-cli && \
    rm -rf /wheels
COPY docker/scripts/healthcheck.py /usr/local/bin/clink-healthcheck.py
WORKDIR /workspace
RUN chown clink:clink /workspace
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 HOME=/home/clink
USER clink
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD ["python", "/usr/local/bin/clink-healthcheck.py"]
CMD ["clink-mcp-server"]
