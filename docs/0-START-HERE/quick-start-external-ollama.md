# Quick Start — Existing Ollama

Start Open Notebook with PostgreSQL/pgvector:

```bash
cp .env.example .env
docker compose up -d --build
```

Add an Ollama credential under **Settings → API Keys**. For Ollama running on the Docker host, use a host-reachable URL such as `http://host.docker.internal:11434` where supported.

For an Ollama service managed in the same Compose project, use `examples/docker-compose-ollama.yml`, which pins the Ollama image rather than following a mutable tag.
