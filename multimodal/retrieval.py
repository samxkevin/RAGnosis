from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from typing import Iterable
from urllib.parse import quote_plus

import requests

from .schemas import Evidence, ImageObservation


class BiomedicalRetriever:
    """Evidence retrieval with PubMed as a standards-based external source.

    Retrieval is deliberately separated from generation so every answer can
    expose the evidence that actually influenced it.
    """

    def __init__(self, timeout: float | None = None) -> None:
        self.timeout = timeout or float(os.getenv("PUBMED_TIMEOUT", "15"))
        self.base = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

    def _query(self, question: str, observations: Iterable[ImageObservation]) -> str:
        terms = [question.strip()]
        terms.extend(o.label for o in observations if o.label.strip())
        terms.extend(o.description for o in observations if o.description.strip())
        text = " ".join(terms)
        return " ".join(text.split())[:900]

    def search(self, question: str, observations: list[ImageObservation], limit: int = 5) -> list[Evidence]:
        query = self._query(question, observations)
        if not query:
            return []

        params = {
            "db": "pubmed",
            "term": query,
            "retmode": "json",
            "retmax": str(max(1, min(limit, 10))),
            "sort": "relevance",
        }
        search = requests.get(f"{self.base}/esearch.fcgi", params=params, timeout=self.timeout)
        search.raise_for_status()
        ids = search.json().get("esearchresult", {}).get("idlist", [])
        if not ids:
            return []

        fetch = requests.get(
            f"{self.base}/efetch.fcgi",
            params={"db": "pubmed", "id": ",".join(ids), "retmode": "xml"},
            timeout=self.timeout,
        )
        fetch.raise_for_status()
        root = ET.fromstring(fetch.text)
        evidence: list[Evidence] = []
        for article in root.findall(".//PubmedArticle"):
            pmid = article.findtext(".//PMID") or ""
            title = "".join(article.find(".//ArticleTitle").itertext()) if article.find(".//ArticleTitle") is not None else "Untitled PubMed article"
            abstract_parts = []
            for node in article.findall(".//Abstract/AbstractText"):
                abstract_parts.append("".join(node.itertext()))
            excerpt = " ".join(abstract_parts).strip()
            if not excerpt:
                excerpt = "Abstract unavailable; retrieve the cited PubMed record for full text metadata."
            evidence.append(
                Evidence(
                    source="PubMed",
                    title=title,
                    excerpt=excerpt[:1200],
                    uri=f"https://pubmed.ncbi.nlm.nih.gov/{quote_plus(pmid)}/" if pmid else None,
                    metadata={"pmid": pmid},
                )
            )
        return evidence
