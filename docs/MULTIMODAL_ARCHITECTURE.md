# RAGnosis Multimodal Architecture

## Purpose

The multimodal track extends RAGnosis from text-only biomedical retrieval into an evidence-grounded research workflow that can accept an image together with a question.

The design intentionally separates four concerns:

1. **Image inspection**: validates the uploaded file and records non-sensitive technical metadata and a SHA-256 digest.
2. **Vision observation**: a configurable vision-language model produces conservative observations. Observations are not diagnoses.
3. **Evidence retrieval**: PubMed is queried using the question and observed concepts. Retrieved records remain explicit evidence objects with titles, excerpts, PMIDs, and URLs.
4. **Grounded generation**: the language model receives the question, observations, retrieved evidence, and safety policy. It must distinguish observations, evidence, uncertainty, and conclusions.

```text
image + question
       |
       v
 image validation / SHA-256
       |
       v
 configurable vision model
       |
       +---- observations + uncertainty
       |
       v
 biomedical retrieval (PubMed)
       |
       +---- cited evidence
       |
       v
 constrained generation
       |
       v
 evidence-grounded response
```

## Why this boundary matters

The project is a research assistant, not a validated diagnostic medical device. The system therefore must not convert a model's visual observation into a definitive disease diagnosis or treatment instruction. A medical-image workflow can have regulatory implications when software is intended to acquire, process, or analyze medical images for clinical purposes, so intended use and validation must remain explicit.

## Provider abstraction

`multimodal/vision.py` uses an OpenAI-compatible HTTP interface. The model and endpoint are configuration values:

- `OPENAI_API_KEY`
- `MULTIMODAL_BASE_URL`
- `MULTIMODAL_VISION_MODEL`
- `MULTIMODAL_TIMEOUT`

This permits a hosted VLM today and a local/self-hosted adapter later without changing the orchestration layer.

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

## Important limitations

- The initial vision adapter is a provider integration, not a clinically validated tumour, cancer, or disease detector.
- No diagnostic performance claim should be made from this repository without task-specific datasets, validation, calibration, and expert review.
- PubMed retrieval improves grounding but does not make retrieved literature patient-specific advice.
- Images are processed temporarily by the API and deleted after the request.
- The current implementation does not persist patient images or biometric representations.
