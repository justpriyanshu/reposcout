# Single-repository image: index a mounted repository and serve the web UI, API and webhook.
#
# Multi-stage build: Node builds the web UI, uv installs the Python dependencies, and the
# final image only gets the virtual environment, the tree-sitter grammars and gitleaks.

# --------------------------------------------------------------------------- web UI
FROM node:24-slim AS web
WORKDIR /app
COPY frontend/package.json frontend/package-lock.json frontend/
RUN npm --prefix frontend ci --no-audit --no-fund
COPY frontend frontend
RUN mkdir -p src/reposcout/static && npm --prefix frontend run build

# --------------------------------------------------------------------------- python
FROM python:3.12-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.12.23 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never UV_NO_CACHE=1
WORKDIR /app
RUN uv venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv PATH=/opt/venv/bin:$PATH
# Dependencies first, so editing the code doesn't reinstall them.
COPY pyproject.toml README.md ./
RUN uv pip install -r pyproject.toml --extra scanners --extra observability
COPY src ./src
COPY --from=web /app/src/reposcout/static/dist ./src/reposcout/static/dist
RUN uv pip install --no-deps .
# Grammars are downloaded at build time, so indexing never needs the network for them.
RUN REPOSCOUT_GRAMMAR_DIR=/opt/grammars reposcout grammars && chmod -R a+rX /opt/grammars

# gitleaks (secret scanner used in reviews), checked against the release's checksums.
ARG GITLEAKS_VERSION=8.30.1
ARG TARGETARCH
COPY deploy/fetch_gitleaks.py /tmp/
RUN python /tmp/fetch_gitleaks.py "$GITLEAKS_VERSION" /opt/gitleaks "$TARGETARCH"

# --------------------------------------------------------------------------- runtime
FROM python:3.12-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/venv /opt/venv
COPY --from=build /opt/grammars /opt/grammars
COPY --from=build /opt/gitleaks /usr/local/bin/gitleaks
ENV PATH=/opt/venv/bin:$PATH \
    REPOSCOUT_GRAMMAR_DIR=/opt/grammars \
    PYTHONUNBUFFERED=1

# Mount the repository to index at /repo. The index is written to /repo/.reposcout/.
ENV REPOSCOUT_REPO_PATH=/repo
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["reposcout", "serve", "--host", "0.0.0.0", "--port", "8000"]
