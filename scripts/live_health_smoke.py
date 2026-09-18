#!/usr/bin/env python3
"""Optional live smoke test for the health-intelligence capability (§23).

This is the ONLY code path that hits the network, and only when you run it
explicitly. It is NOT part of the automated test suite / CI. It fabricates
nothing: if a source is unreachable it is reported as failed.

Usage:
    python scripts/live_health_smoke.py --location "Hyderabad, Telangana, India"
    python scripts/live_health_smoke.py --location "India" --location "Brazil"

Configure sources via HEALTH_SOURCE_FEEDS (see docs) or rely on the built-in
DEFAULT_HEALTH_FEEDS. Respect source terms and rate limits; do not hammer public
health infrastructure.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the repo importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from multimodal.config import MultimodalConfig  # noqa: E402
from multimodal.health_intelligence import HealthIntelligence  # noqa: E402
from multimodal.location import parse_locations  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Live health-intelligence smoke test.")
    parser.add_argument(
        "--location",
        action="append",
        default=[],
        help="Explicit location (repeatable). E.g. --location 'India'.",
    )
    args = parser.parse_args(argv)

    location_text = "; ".join(args.location) if args.location else None
    locations = parse_locations(location_text)

    config = MultimodalConfig.from_env()
    hi = HealthIntelligence(config)

    print("RAGnosis live health-intelligence smoke test")
    print("=" * 60)
    print(f"Requested location(s): {location_text or '(none)'}")
    print(f"Parsed: {[l.normalized for l in locations] or '(none)'}")
    print(f"Cache TTL: {config.health_cache_ttl}s")
    print("-" * 60)

    result = hi.gather(locations)

    print(f"Retrieved at:         {result.retrieved_at}")
    print(f"Live data status:     {result.live_data_status}")
    print(f"Sources attempted:    {', '.join(result.sources_attempted) or '(none)'}")
    print(f"Sources succeeded:    {', '.join(result.sources_succeeded) or '(none)'}")
    print(f"Sources failed:       {', '.join(result.sources_failed) or '(none)'}")
    print(f"From cache:           {result.from_cache}")
    print(f"Findings:             {len(result.findings)}")
    for note in result.notes:
        print(f"  note: {note}")
    print("-" * 60)

    for f in result.findings:
        print(f"* {f.disease_name} [{f.status}] scope={f.geo_scope}")
        print(f"    location:      {f.requested_location} -> {f.relevance_to_location}")
        print(f"    transmission:  {f.transmission_class} (transmissible={f.transmissible})")
        print(f"    risk:          {f.risk_assessment}")
        print(f"    alert:         {f.alert_level}")
        print(f"    freshness:     {f.freshness_state}")
        print(f"    published/updated: {f.published_at} / {f.updated_at}")
        if f.conflict_summary:
            print(f"    conflict:      {f.conflict_summary}")
        if f.uncertainty:
            print(f"    uncertainty:   {f.uncertainty}")
        for s in f.sources:
            print(f"      - {s.organization} ({s.tier}) {s.uri or ''}")

    return 0 if result.live_data_status != "unavailable" else 2


if __name__ == "__main__":
    raise SystemExit(main())
