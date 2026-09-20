# Live-source fixtures

These files are **verbatim samples** captured from the real authoritative
endpoints during Phase-3 hardening (2026-09-20) using out-of-band retrieval.
They exist so the parsing/normalization pipeline can be validated against
*actual* source shapes fully offline (no network in CI).

| File | Source | Endpoint | Captured |
| --- | --- | --- | --- |
| `who_don_sample.json` | WHO Disease Outbreak News | https://www.who.int/api/news/diseaseoutbreaknews | 2026-09-20 |
| `cdc_newsroom_sample.rss` | CDC Newsroom | https://tools.cdc.gov/api/v2/resources/media/132608.rss | 2026-09-20 |
| `ecdc_cdtr_sample.xml` | ECDC CDTR | https://www.ecdc.europa.eu/en/taxonomy/term/2942/feed | 2026-09-20 |
| `paho_sample.rss` | PAHO/WHO Americas | https://www.paho.org/en/rss.xml | 2026-09-20 |

They are trimmed to a few representative items. They are NOT live data and must
not be presented as current outbreak status; they only prove the parsers handle
real source structure.
