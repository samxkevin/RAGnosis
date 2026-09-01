import json
import logging
import os
import threading
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from neo4j import GraphDatabase
import cohere

logging.getLogger("neo4j").setLevel(logging.ERROR)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ragnosis")

ROOT_DIR = Path(__file__).resolve().parent

NEO4J_URI = os.environ.get("NEO4J_URI", "")
NEO4J_USER = os.environ.get("NEO4J_USER", "")
NEO4J_PASSWORD = os.environ.get("NEO4J_PASSWORD", "")
NEO4J_DATABASE = os.environ.get("NEO4J_DATABASE", "")
COHERE_API_KEY = os.environ.get("COHERE_API_KEY", "")


def neo4j_settings():
    return {
        "uri": os.environ.get("NEO4J_URI", ""),
        "user": os.environ.get("NEO4J_USER", ""),
        "password": os.environ.get("NEO4J_PASSWORD", ""),
        "database": os.environ.get("NEO4J_DATABASE", ""),
    }


def cohere_api_key():
    return os.environ.get("COHERE_API_KEY", "")


def _redact(text, secret):
    text = str(text)
    if secret and secret in text:
        return text.replace(secret, "[redacted]")
    return text

PRIMARY_COHERE_MODEL = "command-a-03-2025"
FALLBACK_COHERE_MODEL = "command-r7b-12-2024"

MEDICAL_DISCLAIMER = (
    "RAGnosis is an informational biomedical assistant. It does not provide "
    "professional medical diagnosis or treatment and is not a substitute for "
    "qualified clinical care. Seek in-person medical help for personal health decisions."
)

SEARCH_PROPS = ["name", "text", "description", "disease", "symptom", "title", "canonical_name"]


def _jsonable(value, limit=800):
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= limit else value[:limit] + "..."
    if isinstance(value, list):
        if value and isinstance(value[0], (int, float)) and len(value) > 32:
            return None
        return [_jsonable(v, limit=limit) for v in value[:12]]
    if isinstance(value, dict):
        return {k: _jsonable(v, limit=limit) for k, v in value.items()}
    return str(value)


def _node_properties(node):
    props = {}
    raw = getattr(node, "_properties", None)
    if raw is None:
        try:
            raw = dict(node)
        except Exception:
            return props
    for key, value in raw.items():
        if key.lower() in {"embedding", "embeddings", "vector"}:
            continue
        converted = _jsonable(value)
        if converted is None:
            continue
        props[key] = converted
    return props


class Neo4jConnector:
    def __init__(self, uri, user, password, database=""):
        self.uri = uri
        self.user = user
        self.password = password
        self.database = database
        self.driver = None
        self.last_error = ""

    def configured(self):
        return bool(self.uri and self.user and self.password)

    def _session(self):
        if self.database:
            return self.driver.session(database=self.database)
        return self.driver.session()

    def connect(self):
        if not self.configured():
            self.last_error = "Neo4j environment variables are not set."
            return False
        if self.driver is not None:
            return True
        try:
            self.driver = GraphDatabase.driver(
                self.uri,
                auth=(self.user, self.password),
                connection_timeout=10,
            )
            self.driver.verify_connectivity()
            self.last_error = ""
            logger.info("Connected to Neo4j Aura")
            return True
        except Exception as exc:
            self.last_error = _redact(exc, self.password)
            logger.error("Neo4j connection failed")
            if self.driver is not None:
                try:
                    self.driver.close()
                except Exception:
                    pass
                self.driver = None
            return False

    def close(self):
        if self.driver is not None:
            self.driver.close()
            self.driver = None

    def ping(self):
        if not self.connect():
            return False
        try:
            with self._session() as session:
                session.run("RETURN 1 AS ok").single()
            self.last_error = ""
            return True
        except Exception as exc:
            self.last_error = _redact(exc, self.password)
            return False

    def search_entities(self, query_text):
        if not self.connect():
            raise RuntimeError(self.last_error or "Neo4j is not connected.")

        query_text = (query_text or "").strip()
        if not query_text:
            return "No relevant entities found."

        query_parts = [
            f"(n.{prop} IS NOT NULL AND toLower(toString(n.{prop})) CONTAINS toLower($query))"
            for prop in SEARCH_PROPS
        ]
        where_clause = " OR ".join(query_parts)
        cypher_query = f"""
            MATCH (n)
            WHERE {where_clause}
            RETURN n
            LIMIT 5
        """
        with self._session() as session:
            result = session.run(cypher_query, {"query": query_text[:300]})
            records = result.values()
            context = "\n".join(
                [
                    json.dumps(_node_properties(row[0]), indent=2)
                    for row in records
                    if row
                ]
            )
            return context if context else "No relevant entities found."


class BiomedicalRAG:
    def __init__(self, api_key, model=PRIMARY_COHERE_MODEL):
        self.api_key = api_key or ""
        self.model = model
        self.client = None
        self.last_error = ""

    def configured(self):
        return bool(self.api_key)

    def _client(self):
        if not self.configured():
            raise RuntimeError("COHERE_API_KEY is not set.")
        if self.client is None:
            self.client = cohere.Client(self.api_key)
        return self.client

    def _prompt(self, conversation, context):
        convo_text = "\n".join(
            [
                f"Patient: {c['patient']}\nDoctor: {c.get('doctor', '')}"
                if "doctor" in c
                else f"Patient: {c['patient']}"
                for c in conversation
            ]
        )
        return f"""
You are a kind, logical, biomedical doctor chatbot.

SAFETY:
- You are an informational assistant, not a licensed clinician.
- Do not claim to replace professional diagnosis or treatment.
- Remind the user to seek qualified medical care for personal health decisions.

PATIENT CONVERSATION HISTORY:
{convo_text}

KNOWLEDGE GRAPH CONTEXT:
{context}

TASK:
- Ask relevant medical follow-up questions.
- If enough info, provide the most likely diagnosis and advice.
- Be concise, empathetic, and medically accurate.
- Ground answers in the knowledge graph context when it is available.
- If the knowledge graph context is missing or insufficient, say so.
- End the chat when confident in your diagnosis.

YOUR RESPONSE:
"""

    def _chat(self, prompt, model):
        response = self._client().chat(
            model=model,
            message=prompt,
            temperature=0.4,
        )
        text = getattr(response, "text", None)
        if not text:
            raise RuntimeError("Cohere returned an empty response.")
        return text.strip()

    def answer(self, conversation, context):
        if not self.configured():
            self.last_error = "COHERE_API_KEY is not set."
            return "Cohere is not configured. Set COHERE_API_KEY and retry."
        prompt = self._prompt(conversation, context)
        try:
            return self._chat(prompt, self.model)
        except Exception as exc:
            message = str(exc).lower()
            model_error = any(
                token in message
                for token in ("model", "not found", "decommissioned", "unknown")
            )
            if model_error and self.model != FALLBACK_COHERE_MODEL:
                logger.warning("Primary Cohere model failed; trying fallback model")
                try:
                    text = self._chat(prompt, FALLBACK_COHERE_MODEL)
                    self.model = FALLBACK_COHERE_MODEL
                    self.last_error = ""
                    return text
                except Exception as fallback_exc:
                    self.last_error = _redact(fallback_exc, self.api_key)
                    return f"Error querying Cohere Chat API: {self.last_error}"
            self.last_error = _redact(exc, self.api_key)
            return f"Error querying Cohere Chat API: {self.last_error}"


class DoctorChatPipeline:
    def __init__(self, uri, user, password, api_key, database=""):
        self.neo4j = Neo4jConnector(uri, user, password, database=database)
        self.rag = BiomedicalRAG(api_key)

    def chat(self, message, conversation):
        context = self.neo4j.search_entities(message)
        history = list(conversation or []) + [{"patient": message}]
        reply = self.rag.answer(history, context)
        return reply, context


_pipeline = None
_pipeline_lock = threading.Lock()


def get_pipeline():
    global _pipeline
    if _pipeline is None:
        with _pipeline_lock:
            if _pipeline is None:
                neo4j = neo4j_settings()
                _pipeline = DoctorChatPipeline(
                    neo4j["uri"],
                    neo4j["user"],
                    neo4j["password"],
                    cohere_api_key(),
                    database=neo4j["database"],
                )
    return _pipeline


app = Flask(__name__)
CORS(app)


@app.route("/", methods=["GET"])
def index():
    return send_from_directory(ROOT_DIR, "index.html")


@app.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    conversation = data.get("conversation") or []
    if not message:
        return jsonify({"response": "Please enter a message.", "error": "empty_message"}), 400
    if not isinstance(conversation, list):
        conversation = []

    pipeline = get_pipeline()
    try:
        doctor_reply, context = pipeline.chat(message, conversation)
        return jsonify(
            {
                "response": doctor_reply,
                "disclaimer": MEDICAL_DISCLAIMER,
                "context_found": bool(context and context != "No relevant entities found."),
            }
        )
    except Exception as exc:
        safe = _redact(exc, neo4j_settings()["password"])
        logger.error("Chat failed")
        return jsonify(
            {
                "response": f"Knowledge retrieval failed: {safe}",
                "error": safe,
                "disclaimer": MEDICAL_DISCLAIMER,
            }
        ), 503


@app.route("/health", methods=["GET"])
def health():
    pipeline = get_pipeline()
    neo4j_configured = pipeline.neo4j.configured()
    neo4j_connected = pipeline.neo4j.ping() if neo4j_configured else False
    return jsonify(
        {
            "status": "ok",
            "disclaimer": MEDICAL_DISCLAIMER,
            "neo4j": {
                "configured": neo4j_configured,
                "connected": neo4j_connected,
                "database": neo4j_settings()["database"] or None,
                "error": pipeline.neo4j.last_error or None,
            },
            "cohere": {
                "configured": pipeline.rag.configured(),
                "model": pipeline.rag.model,
            },
        }
    )


if __name__ == "__main__":
    port = int(os.environ["PORT"]) if os.environ.get("PORT") else 8000
    app.run(host="0.0.0.0", port=port)
