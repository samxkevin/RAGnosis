# RAGnosis

RAGnosis is a biomedical retrieval-augmented chatbot. A patient describes symptoms in a web chat. The application retrieves related entities from a Neo4j knowledge graph, then Cohere generates a follow-up or advice reply grounded in that context.

This repository's deployed application is a Flask service with a browser chat UI. It is informational only and is not a substitute for professional medical diagnosis or treatment.

## Architecture

```
User
  |
  v
Text chat (browser UI)
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

The web service does not run Neo4j locally. Production retrieval uses a remote Neo4j Aura instance over `neo4j+s://`.

## Main components

- `app.py`: Flask entry point, Neo4j retrieval, Cohere generation, `/chat` and `/health` routes.
- `index.html`: RAGnosis text-chat UI served by Flask at `/`.
- `Work/doctor_chatbot.py`: original command-line chatbot using the same Neo4j + Cohere pattern.
- `EmbeddingsIngestion.py`, `EmbeddingsEnrichmentEncrpted.py`, `GraphReconstructionEncrypted.py`: offline graph construction and enrichment scripts. They are not required to start the web service.
- `Database/`: graph export, dump, and enrichment CSVs used to build the knowledge graph.

## Neo4j Aura

Set these environment variables:

- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`

The application reads `NEO4J_USER` first and falls back to `NEO4J_USERNAME` if present. Do not put the password in source, README, or logs.

The Flask process connects lazily. If Aura is paused or unreachable, the UI still loads. Chat and other database-dependent actions report the connection error instead of pretending the graph is available.

Retrieval is keyword `CONTAINS` search over node properties (`name`, `text`, `description`, `disease`, `symptom`, `title`, `canonical_name`), limited to five nodes. Embedding vectors are stripped from the context sent to Cohere.

## Cohere

Set `COHERE_API_KEY`.

The application uses Cohere's Chat API through `cohere.Client`. The primary model is `command-a-03-2025`, with fallback to `command-r7b-12-2024` if the primary model is unavailable. If the API key is missing or the request fails, RAGnosis returns that error. It does not invent a diagnosis.

## Voice models

This repository does not include Whisper speech-to-text, XTTS text-to-speech, or a voice-chat interface. The deployed service is text-only.

## Gradio

This repository does not include a Gradio interface. The public UI is the Flask-served `index.html` chat page.

## Knowledge base

The biomedical graph lives in Neo4j Aura. Offline scripts and `Database/` files are used to reconstruct, enrich, and ingest graph data. The running web app queries that remote graph; it does not seed duplicate facts on every startup.

## Conversation persistence

The browser keeps the current multi-turn transcript and sends it with each `/chat` request. The original CLI chatbot in `Work/doctor_chatbot.py` keeps conversation state in memory for that process and prints a summary on exit. Render Free instances are ephemeral, so in-process memory is not a durable store across restarts or idle spin-down.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `NEO4J_URI` | yes for retrieval | Aura bolt URI, for example `neo4j+s://xxxxx.databases.neo4j.io` |
| `NEO4J_USER` | yes for retrieval | Aura username |
| `NEO4J_PASSWORD` | yes for retrieval | Aura password |
| `NEO4J_DATABASE` | recommended | Aura database name |
| `COHERE_API_KEY` | yes for generation | Cohere API key |
| `PORT` | set by Render | HTTP port; local default is `10000` |

Copy `.env.example` and export the values in your shell. Do not commit real credentials.

## Local setup

Python 3.11 is the documented runtime.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export NEO4J_URI="neo4j+s://xxxxx.databases.neo4j.io"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="your-aura-password"
export NEO4J_DATABASE="neo4j"
export COHERE_API_KEY="your-cohere-key"
python app.py
```

Then open `http://127.0.0.1:10000`.

CLI chatbot:

```bash
python Work/doctor_chatbot.py
```

## Render deployment

Deploy as a Render Web Service from this repository.

- Runtime: Python 3.11
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
- Health check path: `/health`
- Bind host: `0.0.0.0`
- Port: Render `$PORT` (default `10000`)

`render.yaml` describes the same service. In the Render dashboard, create a Web Service, point it at this repo, choose the Free compute plan if needed, and add the Neo4j and Cohere environment variables. Do not enable Gradio `share=True`; Render provides the public URL.

Render Free has no GPU and spins down after about 15 minutes idle. The first request after spin-down can take around a minute. That is acceptable here because the web app does not load local speech models.

## Medical safety disclaimer

RAGnosis is an informational biomedical assistant. It does not provide professional medical diagnosis or treatment and is not a substitute for qualified clinical care. Seek in-person medical help for personal health decisions.

## Security

Credentials belong in environment variables, not in source. Previous hardcoded Neo4j passwords in this project should be treated as compromised and rotated in Aura.
