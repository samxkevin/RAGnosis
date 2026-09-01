# RAGnosis

[![Python 3.11](https://img.shields.io/badge/python-3.11-3776AB?logo=python&logoColor=white)](https://docs.python.org/3.11/)
[![Flask](https://img.shields.io/badge/Flask-3.x-000000?logo=flask&logoColor=white)](https://flask.palletsprojects.com/)
[![Render](https://img.shields.io/badge/Render-web%20service-46E3B7?logo=render&logoColor=white)](https://render.com/docs/web-services)

RAGnosis is a text-only biomedical retrieval-augmented chatbot. A user describes symptoms in a browser chat. Flask retrieves related entities from a remote [Neo4j Aura](https://neo4j.com/product/auradb/) knowledge graph, then [Cohere](https://docs.cohere.com/) generates a follow-up or advice reply from that context.

The production application is the Flask service at the repository root (`app.py` and `index.html`). It is designed to run as a Render web service with gunicorn. Experimental notebooks, Colab pipeline exports, and graph-construction utilities live under `experiments/` and `scripts/`. Those files document how the graph and RAG prototype were built. They are not the Render application.

RAGnosis is informational only. It does not provide professional medical diagnosis or treatment and is not a substitute for qualified clinical care. Seek in-person medical help for personal health decisions.

## How it works

The production path is a single request pipeline:

```
Browser (index.html)
        |
        |  GET /           chat UI
        |  GET /health     process and configuration status
        |  POST /chat      retrieval, then generation
        v
Flask (app.py)
        |
        +--> Neo4j Aura    keyword retrieval
        |
        +--> Cohere Chat   response generation
        v
Text reply in the web interface
```

1. `index.html` is a multi-turn text chat. The current transcript is kept in the page as `{patient, doctor}` turns and posted with each message. There is no durable server-side conversation store.
2. `POST /chat` asks Neo4j Aura for related nodes. Retrieval is Cypher `CONTAINS` matching over `name`, `text`, `description`, `disease`, `symptom`, `title`, and `canonical_name`, limited to five nodes. Embedding vectors are stripped before context is sent to the model.
3. Cohere Chat (`cohere.Client`) generates the reply with primary model `command-a-03-2025` and fallback `command-r7b-12-2024` if the primary model is unavailable.
4. The UI shows Neo4j and Cohere status from `GET /health`, plus the medical disclaimer.

Neo4j is not started inside the web process. The app connects lazily to a remote Aura instance (`neo4j+s://`). Flask and `/health` still respond if Aura is paused, unreachable, or unconfigured. `/chat` returns the real retrieval or generation error. It does not invent graph hits or a diagnosis when Neo4j or Cohere is unavailable.

This production service is text chat only. It does not include voice input, speech synthesis, Gradio, image upload, or notebook-only RAG variants.

## Repository structure

```
app.py              Production Flask entry point (gunicorn app:app)
index.html          Production text-chat UI
requirements.txt    Production Python dependencies
render.yaml         Render web service settings
.python-version     Python 3.11
.env.example        Environment variable names (no secrets)
Database/           Offline graph export and enrichment CSVs
scripts/            CLI chatbot and graph ingestion utilities
experiments/        Notebooks and Colab pipeline exports (not deployed)
```

| Path | Role |
| --- | --- |
| `app.py`, `index.html` | Production web application |
| `requirements.txt`, `render.yaml`, `.python-version` | Production runtime and Render config |
| `Database/` | Offline graph files used by ingestion and research, not loaded by gunicorn at startup |
| `scripts/doctor_chatbot.py` | Terminal chatbot with the same Neo4j plus Cohere idea |
| `scripts/EmbeddingsIngestion.py` | Similarity-link ingestion into Aura from `Database/EnrichmentReport` |
| `experiments/` | Historical and experimental work; see `experiments/README.md` |

## HTTP API

| Method | Path | Behavior |
| --- | --- | --- |
| `GET` | `/` | Serves `index.html` |
| `GET` | `/health` | Returns process liveness (`status: ok`) and whether Neo4j and Cohere are configured. HTTP 200 even if Aura or Cohere is down. |
| `POST` | `/chat` | JSON `{ "message": "...", "conversation": [] }`. Retrieves graph context, then generates a reply. Empty messages return 400. Retrieval failure returns 503 with an error payload, not a fake answer. |

## Configuration

Credentials come only from the process environment. Copy `.env.example`. Do not commit real values.

| Variable | Required | Purpose |
| --- | --- | --- |
| `NEO4J_URI` | Yes, for retrieval | Aura URI |
| `NEO4J_USER` | Yes, for retrieval | Aura username |
| `NEO4J_PASSWORD` | Yes, for retrieval | Aura password |
| `NEO4J_DATABASE` | Recommended | Aura database name |
| `COHERE_API_KEY` | Yes, for generation | Cohere API key |
| `PORT` | Set by the host | HTTP port for gunicorn |

Render provides `PORT` dynamically. Do not hardcode the service port. The names above are the only application environment variables. There is no `NEO4J_USERNAME` alias.

## Local development

Python 3.11, as recorded in `.python-version`.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export NEO4J_URI=
export NEO4J_USER=
export NEO4J_PASSWORD=
export NEO4J_DATABASE=
export COHERE_API_KEY=
python app.py
```

Without `PORT`, `python app.py` listens on `8000`. gunicorn should bind the host-provided `PORT`:

```bash
gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120
```

Optional terminal chatbot (not the web service):

```bash
python scripts/doctor_chatbot.py
```

## Render deployment

Create a Render Web Service from this repository. `render.yaml` matches these settings:

- Runtime: Python 3.11
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
- Health check path: `/health`
- Bind host: `0.0.0.0`
- Bind port: `$PORT` (injected by Render)

Set `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, and `COHERE_API_KEY` in the Render dashboard for the current Aura instance and current Cohere key. Do not paste those values into the repository.

Render Free has no GPU and spins down after about 15 minutes idle. The first request after spin-down can take about a minute. The filesystem is ephemeral, which is why conversation history stays in the browser.

## Experiments and reference work

`experiments/` is not served by gunicorn and is not part of the Render production path.

It holds Colab RAG notebooks, graph enrichment and reconstruction exports, and milestone diagrams. Those artifacts may use different LLM backends, Colab paths, or retrieval strategies than `app.py`. Treat them as historical reference. Details are in `experiments/README.md`.

`scripts/` is also outside the web entry point. `EmbeddingsIngestion.py` reads `NEO4J_*` from the environment and CSVs from `Database/EnrichmentReport` (or `ENRICHMENT_DIR`). It is a maintenance utility, not a request handler.

## Checks

This repository does not include an automated test suite. After install, confirm:

- `GET /` returns the chat page
- `GET /health` returns HTTP 200 with `status: ok` even when Neo4j and Cohere are unset
- `POST /chat` returns a real configuration or connection error when retrieval cannot run
- gunicorn binds `0.0.0.0` and `$PORT`

## Scope and limitations

- Production is Flask, remote Neo4j Aura, Cohere Chat, and a static HTML UI.
- Retrieval is keyword `CONTAINS` search, not vector similarity search in the web app.
- The live Aura graph is not defined by files in `Database/`. Those files are offline exports and enrichment inputs.
- Render Free is CPU-only and may sleep when idle.
- Older Git history on this GitHub repository may still contain leaked credentials. Treat those values as compromised and rotate them. Current tracked files expect secrets only in environment variables.

## Medical disclaimer

RAGnosis is an informational biomedical assistant. It does not provide professional medical diagnosis or treatment and is not a substitute for qualified clinical care. Seek in-person medical help for personal health decisions.
