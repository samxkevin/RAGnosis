# RAGnosis

RAGnosis is a biomedical retrieval-augmented chatbot. A user describes symptoms in a browser text chat. Flask retrieves related entities from a remote Neo4j Aura knowledge graph, then Cohere generates a follow-up or advice reply using that context.

The deployed application in this repository is text-only Flask. It is informational only and is not a substitute for professional medical diagnosis or treatment.

## Architecture

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

Neo4j is not started inside the Render process. Production retrieval uses a remote Aura instance over `neo4j+s://`.

## What this repository implements

Reachable in the running web app:

- Multi-turn text chat at `/`
- `POST /chat` retrieval-augmented replies
- `GET /health` process liveness and Neo4j/Cohere status
- Neo4j Aura keyword retrieval
- Cohere Chat generation
- Medical safety disclaimer in the UI, `/health`, and model prompt
- Browser-side conversation transcript sent with each chat request

Not implemented in this repository, and not part of the Render service:

- Gradio
- Whisper speech-to-text
- XTTS or other local text-to-speech
- Voice chat
- Image upload
- Neo4j full-text indexes
- Durable server-side conversation storage

A separate notebook prototype may contain experimental Gradio, Whisper, XTTS, or image-upload work. Those pieces are not in the GitHub application entry point and are not deployed.

## Main components

- `app.py`: Flask entry point, lazy Neo4j connector, Cohere generation, `/`, `/chat`, and `/health`.
- `index.html`: text-chat UI served at `/`.
- `Work/doctor_chatbot.py`: command-line chatbot using the same Neo4j + Cohere pattern.
- `EmbeddingsIngestion.py`, `EmbeddingsEnrichmentEncrpted.py`, `GraphReconstructionEncrypted.py`: offline graph construction scripts. They are not required to start the web service.
- `Database/`: graph export, dump, and enrichment files used to build the knowledge graph offline.
- `render.yaml`: Render Web Service settings.

## Neo4j Aura

Environment variables:

- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `NEO4J_DATABASE`

The application reads `NEO4J_USER`, then `NEO4J_USERNAME` if `NEO4J_USER` is empty. Credentials are not stored in source.

The driver is created lazily on first use. If Aura is paused or unreachable, `/` and `/health` still respond. `/chat` returns the connection error and does not pretend retrieval succeeded.

Retrieval is Cypher `CONTAINS` matching over `name`, `text`, `description`, `disease`, `symptom`, `title`, and `canonical_name`, limited to five nodes. Embedding vectors are removed before context is sent to Cohere.

## Cohere

Set `COHERE_API_KEY`.

Generation uses `cohere.Client` Chat with primary model `command-a-03-2025` and fallback `command-r7b-12-2024` if the primary model is unavailable. A missing key or API error is returned to the user. The app does not fabricate a diagnosis when Cohere is unavailable.

## Conversation persistence

The browser keeps the current transcript and posts it to `/chat` as `conversation`. The CLI chatbot keeps memory only for that process. Render Free filesystems are ephemeral, so there is no durable conversation store across restarts or idle spin-down.

## Environment variables

| Variable | Required | Purpose |
| --- | --- | --- |
| `NEO4J_URI` | yes for retrieval | Aura URI, for example `neo4j+s://xxxxxxxx.databases.neo4j.io` |
| `NEO4J_USER` | yes for retrieval | Aura username |
| `NEO4J_PASSWORD` | yes for retrieval | Aura password |
| `NEO4J_DATABASE` | recommended | Aura database name |
| `COHERE_API_KEY` | yes for generation | Cohere API key |
| `PORT` | set by Render | HTTP port; local default is `10000` |

Copy `.env.example`. Do not commit real values.

## Local setup

Python 3.11.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
export NEO4J_URI="neo4j+s://xxxxxxxx.databases.neo4j.io"
export NEO4J_USER="neo4j"
export NEO4J_PASSWORD="your-aura-password"
export NEO4J_DATABASE="neo4j"
export COHERE_API_KEY="your-cohere-key"
python app.py
```

Open `http://127.0.0.1:10000`.

CLI:

```bash
python Work/doctor_chatbot.py
```

## Render deployment

Web Service:

- Runtime: Python 3.11
- Build command: `pip install -r requirements.txt`
- Start command: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --timeout 120`
- Health check path: `/health`
- Host: `0.0.0.0`
- Port: `$PORT` (Render default `10000`)

Set the Neo4j and Cohere variables in the Render dashboard. Render provides the public URL.

Render Free has no GPU and spins down after about 15 minutes idle. The first request after spin-down can take about a minute. This text-only service does not load local speech models.

## Medical safety disclaimer

RAGnosis is an informational biomedical assistant. It does not provide professional medical diagnosis or treatment and is not a substitute for qualified clinical care. Seek in-person medical help for personal health decisions.

## Security

Use environment variables only. Do not put Aura passwords or Cohere keys in source, README, or logs.

Older Git commits on this GitHub repository still contain leaked Neo4j and Cohere credentials. Those values must be treated as compromised and rotated in Aura and Cohere. Current `HEAD` does not contain those secrets.
