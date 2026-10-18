FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Mount the repository to index at /repo. The index is written to /repo/.reposcout/.
ENV REPOSCOUT_REPO_PATH=/repo
EXPOSE 8000
HEALTHCHECK CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["reposcout", "serve", "--host", "0.0.0.0", "--port", "8000"]
