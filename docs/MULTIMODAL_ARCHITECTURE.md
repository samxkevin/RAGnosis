# RAGnosis Multimodal Architecture

## Purpose

The multimodal track extends RAGnosis from text-only biomedical retrieval into an evidence-grounded research workflow that can accept an image together with a question.

The design intentionally separates concerns so that no single stage can silently
turn an uncertain observation into a confident medical claim:

1. **Configuration** (`multimodal/config.py`): every environment-driven setting resolved in one place; no secrets read at import time.
2. **Image inspection** (`multimodal/image.py`): validates the uploaded file, guards against corruption and decompression bombs, records non-sensitive technical metadata and a SHA-256 digest, and normalizes the image for transport.
3. **Vision observation** (`multimodal/vision.py`): a configurable vision-language model produces conservative observations. Observations are not diagnoses. Parsing is a pure function; the HTTP call is injectable.
4. **Evidence retrieval** (`multimodal/retrieval.py`): PubMed is queried using the question and observed concepts. Retrieved records remain explicit evidence objects with titles, excerpts, PMIDs, and URLs. Retrieval degrades gracefully when the network is unavailable.
5. **Grounded generation** (`multimodal/service.py`): the language model receives the question, observations, retrieved evidence, and safety policy. It must distinguish observations, evidence, uncertainty, and conclusions.
6. **Deterministic safety validation** (`multimodal/safety.py`): after generation, code (not the model) checks the answer for definitive-diagnosis language and citations that were never retrieved, records warnings, and appends a standing safety notice.

```text
image + question
       |
       v
 config (env)
       |
       v
 image validation / SHA-256 / normalize
       |
       v
 configurable vision model  --> observations + uncertainty (parsed, clamped)
       |
       v
 biomedical retrieval (PubMed) --> cited evidence  (graceful on failure)
       |
       v
 constrained generation (Cohere, primary + fallback)
       |
       v
 deterministic safety validation --> warnings + safety notice
       |
       v
 evidence-grounded response
```

## Data flow and boundaries

- **Extraction, normalization, computation, decision, explanation, and evaluation are separate modules.** This makes each stage independently testable and keeps the failure surface small.
- **The model never invents numerical or factual grounding.** Confidence values are clamped to `[0, 1]` or dropped; PMIDs cited in the answer are checked against the evidence actually retrieved; retrieval status is passed into the prompt so the model is told explicitly when *not* to cite.
- **Every collaborator is injectable.** `VisionProvider` takes a `poster`, `BiomedicalRetriever` takes a `requests.Session`, and `MultimodalRAGService` takes `vision`, `retriever`, and `generator`. The entire pipeline can therefore be exercised offline with no API keys and no network.

## Why this boundary matters

The project is a research assistant, not a validated diagnostic medical device. The system therefore must not convert a model's visual observation into a definitive disease diagnosis or treatment instruction. A medical-image workflow can have regulatory implications when software is intended to acquire, process, or analyze medical images for clinical purposes, so intended use and validation must remain explicit.

## Preventing hallucination

Three independent mechanisms, none of which trusts the model to police itself:

1. **Prompt discipline** — the shared safety instruction (`safety.build_system_instruction`) forbids diagnosis, fabricated sources, and identity inference, and demands separation of observation vs. evidence vs. uncertainty.
2. **Structured grounding** — observations and evidence are passed as JSON; the retrieval status tells the model whether any literature exists to cite.
3. **Deterministic post-checks** — `safety.validate_response` runs after generation. It flags definitive-diagnosis phrasing and any PMID not present in the retrieved evidence, and guarantees a safety notice. These checks are pure functions, fully unit-tested, and cannot be overridden by model output.

## Provider abstraction

`multimodal/vision.py` uses an OpenAI-compatible HTTP interface. The model and endpoint are configuration values:

- `OPENAI_API_KEY`
- `MULTIMODAL_BASE_URL`
- `MULTIMODAL_VISION_MODEL`
- `MULTIMODAL_TIMEOUT`

This permits a hosted VLM today and a local/self-hosted adapter later without changing the orchestration layer. Generation is likewise configurable (`COHERE_MULTIMODAL_RAG_MODEL`, `COHERE_MULTIMODAL_FALLBACK_MODEL`) with automatic fallback when the primary model is unavailable.

## Configuration reference

| Variable | Purpose | Default |
| --- | --- | --- |
| `OPENAI_API_KEY` | Vision provider key | (unset) |
| `MULTIMODAL_BASE_URL` | OpenAI-compatible base URL | `https://api.openai.com/v1` |
| `MULTIMODAL_VISION_MODEL` | Vision model id | `gpt-4.1-mini` |
| `MULTIMODAL_TIMEOUT` | Vision HTTP timeout (s) | `90` |
| `COHERE_API_KEY` | Generation key | (unset) |
| `COHERE_MULTIMODAL_RAG_MODEL` | Primary generation model | `command-a-03-2025` |
| `COHERE_MULTIMODAL_FALLBACK_MODEL` | Fallback generation model | `command-r7b-12-2024` |
| `PUBMED_TIMEOUT` | PubMed HTTP timeout (s) | `15` |
| `PUBMED_TOOL` / `PUBMED_EMAIL` / `PUBMED_API_KEY` | NCBI etiquette / rate limit | `RAGnosis` / (unset) / (unset) |
| `MULTIMODAL_MAX_UPLOAD_BYTES` | Max upload size | `12582912` |
| `MULTIMODAL_MAX_IMAGE_PIXELS` | Decompression-bomb cap | `40000000` |
| `MULTIMODAL_MAX_VISION_DIMENSION` | Longest edge sent to VLM | `2048` |

## API

Run:

```bash
python multimodal_api.py
```

Health:

```bash
curl http://localhost:8001/health
```

Analysis:

```bash
curl -X POST http://localhost:8001/analyze \
  -F "question=Describe the observable features that should be reviewed by a clinician" \
  -F "image=@sample.png"
```

The response includes:

- `answer`
- `modality`
- `observations`
- `evidence`
- `limitations`
- `vision_model`
- `generation_model`
- `retrieval_status` (`ok` | `empty` | `unavailable` | `skipped`)
- `warnings` (deterministic safety-validation findings)
- `image_metadata`

Status codes: `400` for client input problems (missing question/image, unsupported type, corrupt/oversized image), `413` for uploads over the size limit, `502` for upstream provider/generation failures.

## Testing

```bash
pip install -r requirements-dev.txt
pytest
```

The suite runs entirely offline (no API keys, no network): vision HTTP, PubMed HTTP, and generation are all injected. Coverage spans image validation and normalization, VLM JSON parsing (clean, fenced, malformed), PubMed query building and XML parsing (dedup, missing abstracts, malformed), retrieval network degradation, the deterministic safety validator, end-to-end orchestration, and the HTTP API contract.

## Failure modes and how they are handled

| Failure | Behaviour |
| --- | --- |
| Missing / corrupt / oversized image | `ImageValidationError` -> HTTP 400 |
| Vision provider not configured | `RuntimeError` -> HTTP 502 |
| Vision returns malformed JSON | Parsed to zero observations; pipeline continues |
| PubMed unreachable / throttled | `retrieval_status="unavailable"`, empty evidence, generation continues |
| No literature found | `retrieval_status="empty"`; model told not to cite |
| Primary generation model unavailable | Automatic fallback model |
| Model overstates certainty / invents a PMID | Flagged in `warnings`; safety notice appended |

## Important limitations

- The vision adapter is a provider integration, not a clinically validated tumour, cancer, or disease detector.
- No diagnostic performance claim should be made from this repository without task-specific datasets, validation, calibration, and expert review.
- PubMed retrieval improves grounding but does not make retrieved literature patient-specific advice.
- Images are processed temporarily by the API and deleted after the request.
- The current implementation does not persist patient images or biometric representations.
- The safety validator is a conservative heuristic layer: it reduces, but cannot mathematically guarantee the absence of, overconfident or fabricated output.

## How this scales

- Stateless request handling behind gunicorn; workers scale horizontally.
- Injectable collaborators allow swapping a hosted VLM for a self-hosted one, or PubMed for an internal evidence index, without touching orchestration.
- The pure parsing/validation functions are cheap and deterministic, so throughput is bounded by the external providers, which can be pooled, cached, or rate-limited independently.
