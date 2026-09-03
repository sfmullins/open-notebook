#!/usr/bin/env python3
"""One-shot README patch; deletes itself after a successful rewrite."""

from pathlib import Path

path = Path("README.md")
text = path.read_text(encoding="utf-8")

old_stack = "[![Python][Python]][Python-url] [![Next.js][Next.js]][Next-url] [![React][React]][React-url] [![SurrealDB][SurrealDB]][SurrealDB-url] [![LangChain][LangChain]][LangChain-url]"
new_stack = "[![Python][Python]][Python-url] [![Next.js][Next.js]][Next-url] [![React][React]][React-url] [![PostgreSQL][PostgreSQL]][PostgreSQL-url] [![LangChain][LangChain]][LangChain-url]"
if text.count(old_stack) != 1:
    raise SystemExit(f"expected one Built With line, found {text.count(old_stack)}")
text = text.replace(old_stack, new_stack, 1)

start_marker = "## 🚀 Quick Start (2 Minutes)"
end_marker = "## Star History"
start = text.index(start_marker)
end = text.index(end_marker, start)
quick_start = '''## 🚀 Quick Start

### Prerequisites

- Docker Desktop / Docker Engine with Compose support
- Git
- API keys are configured later in the UI

### Step 1: Clone and configure

```bash
git clone https://github.com/lfnovo/open-notebook.git
cd open-notebook
cp .env.example .env
```

Set strong local values in `.env` for at least:

```dotenv
POSTGRES_USER=open_notebook
POSTGRES_PASSWORD=replace-with-a-strong-database-password
POSTGRES_DB=open_notebook
OPEN_NOTEBOOK_ENCRYPTION_KEY=replace-with-a-long-secret
```

The repository `docker-compose.yml` runs PostgreSQL + pgvector as a separate persistent service and builds the application runtime against it. PostgreSQL is the only runtime database.

### Step 2: Start services

```bash
docker compose up -d --build
```

Check startup:

```bash
docker compose ps
docker compose logs -f open_notebook
```

Then open **http://localhost:8502**. The REST API is available on **http://localhost:5055**.

### Step 3: Configure an AI provider

1. Open **Settings / API Keys**.
2. Add the provider you want to use.
3. Save and test the credential.
4. Discover/register models and assign defaults.

For fully local inference, configure a separately managed Ollama or compatible OpenAI-style endpoint.

> Existing installations from the previous database architecture must use the one-time migration utility before retiring the old store. See [Local Development Setup](docs/7-DEVELOPMENT/development-setup.md#one-time-migration-from-a-legacy-data-store).

### 📚 More Installation Options

- **[Docker Compose](docs/1-INSTALLATION/docker-compose.md)** - Supported packaged topology with PostgreSQL + pgvector
- **[From Source](docs/1-INSTALLATION/from-source.md)** - Native developer/contributor setup
- **[Complete Installation Guide](docs/1-INSTALLATION/index.md)** - Supported deployment routes

---

### 📖 Need Help?

- **🤖 AI Installation Assistant**: [CustomGPT to help you install](https://chatgpt.com/g/g-68776e2765b48191bd1bae3f30212631-open-notebook-installation-assistant)
- **🆘 Troubleshooting**: [5-minute troubleshooting guide](docs/6-TROUBLESHOOTING/quick-fixes.md)
- **💬 Community Support**: [Discord Server](https://discord.gg/37XJPXfz2w)
- **🐛 Report Issues**: [GitHub Issues](https://github.com/lfnovo/open-notebook/issues)

---

'''
text = text[:start] + quick_start + text[end:]

old_current = "**Current Tech Stack**: Python, FastAPI, Next.js, React, SurrealDB"
new_current = "**Current Tech Stack**: Python, FastAPI, Next.js, React, PostgreSQL + pgvector"
if text.count(old_current) != 1:
    raise SystemExit(f"expected one Current Tech Stack line, found {text.count(old_current)}")
text = text.replace(old_current, new_current, 1)

old_refs = "[SurrealDB]: https://img.shields.io/badge/SurrealDB-FF5E00?style=for-the-badge&logo=databricks&logoColor=white\n[SurrealDB-url]: https://surrealdb.com/"
new_refs = "[PostgreSQL]: https://img.shields.io/badge/PostgreSQL-4169E1?style=for-the-badge&logo=postgresql&logoColor=white\n[PostgreSQL-url]: https://www.postgresql.org/"
if text.count(old_refs) != 1:
    raise SystemExit(f"expected one database badge reference block, found {text.count(old_refs)}")
text = text.replace(old_refs, new_refs, 1)

path.write_text(text, encoding="utf-8")
Path(__file__).unlink()
