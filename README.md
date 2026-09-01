# RAGnosis

RAGnosis is a biomedical retrieval-augmented chatbot. A user describes symptoms in a browser text chat. Flask retrieves related entities from a remote Neo4j Aura knowledge graph, then Cohere generates a follow-up or advice reply from that context.

This Render deployment is the text RAG application in `app.py` and `index.html`. It is informational only and is not a substitute for professional medical diagnosis or treatment.

## Repository layout

```
app.py                 Production Flask entry point (gunicorn app:app)
index.html             Production text-chat UI
requirements.txt       Production Python dependencies
render.yaml            Render Web Service settings
.env.example           Environment variable names (no secrets)
Database/              Offline graph export and enrichment CSVs
scripts/               CLI chatbot and graph ingestion utilities
experiments/           Notebooks and Colab pipeline exports (not deployed)
```

## Production application

```
User
  |
  v
Text chat (index.html)
  |
  v
Flask app.py
  |
  +--> Neo4j Aura keyword retrieval
  |       |
  |       v
  |     Medical graph context
  |
  +--> Cohere Chat API
          |
          v
        Text response
```

`app.py` is the production entry point.

- `GET /` serves the text-chat UI
- `POST /chat` runs retrieval and generation
- `GET /health` reports process liveness and whether Neo4j and Cohere are configured

The UI is `index.html`: RAGnosis branding, a multi-turn transcript, a symptom input, Neo4j/Cohere status, and the medical disclaimer.

Neo4j is not started inside the Render process. Retrieval uses a remote Aura instance over `neo4j+s://`.

The driver connects lazily. Retrieval is Cypher `CONTAINS` matching over `name`, `text`, `description`, `disease`, `symptom`, `title`, and `canonical_name`, limited to five nodes. Embedding vectors are stripped before context is sent to Cohere.

If Aura is paused or unreachable, `/` and `/health` still respond. `/chat` returns the connection error and does not pretend retrieval succeeded.

Generation uses `cohere.Client` Chat with primary model `command-a-03-2025` and fallback `command-r7b-12-2024` if the primary model is unavailable. A missing key or API error is returned to the user. The app does not fabricate a diagnosis when Cohere is unavailable.

The browser stores the current transcript as `{patient, doctor}` turns and posts that array to `/chat`. Render Free filesystems are ephemeral, so there is no durable server-side conversation store.

## Experimental and notebook work

See `experiments/README.md`.

That folder holds Colab RAG notebooks and graph-construction exports. They are historical/reference material. They are not served by gunicorn and are not part of the Render production path.

The CLI chatbot is `scripts/doctor_chatbot.py`. Graph similarity ingestion is `scripts/EmbeddingsIngestion.py`.

## Configuration and environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `NEO4J_URI` | yes for retrieval | Aura URI |
| `NEO4J_USER` | yes for retrieval | Aura username |
| `NEO4J_PASSWORD` | yes for retrieval | Aura password |
| `NEO4J_DATABASE` | recommended | Aura database name |
| `COHERE_API_KEY` | yes for generation | Cohere API key |
| `PORT` | set by the host | HTTP port for gunicorn |

These names are read only from the process environment. Copy `.env.example`. Do not commit real values. Render injects `PORT`; do not hardcode the service port.

## Development

Python 3.11.

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

Without `PORT`, local `python app.py` listens on `8000`. With gunicorn, bind `$PORT`.

```bash
python scripts/doctor_chatbot.py
```

## Render deployment

Web Service:

- Runtime: Python 3.11
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
- Health check path: `/health`
- Host: `0.0.0.0`
- Port: `$PORT`

Set `NEO4J_URI`, `NEO4J_USER`, `NEO4J_PASSWORD`, `NEO4J_DATABASE`, and `COHERE_API_KEY` in the Render dashboard for the current Aura instance and current Cohere key. Do not paste those values into the repository. Render provides the public URL.

Render Free has no GPU and spins down after about 15 minutes idle. The first request after spin-down can take about a minute.

## Medical safety disclaimer

RAGnosis is an informational biomedical assistant. It does not provide professional medical diagnosis or treatment and is not a substitute for qualified clinical care. Seek in-person medical help for personal health decisions.

## Security

Use environment variables only. Do not put Aura passwords or Cohere keys in source, README, or logs.

Older Git commits on this GitHub repository still contain leaked credentials. Treat those values as compromised and rotate them. Current `HEAD` does not contain those secrets.
