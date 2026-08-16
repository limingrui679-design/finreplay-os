"""Show why live validation and historical replay eligibility are different sets."""

from finreplay.catalog import load_adapter_catalog

catalog = load_adapter_catalog()
eligible = [adapter for adapter in catalog.adapters if adapter.historical_replay_eligible]

print(f"formal_live_adapters={catalog.adapter_count}")
print(f"historical_replay_eligible={len(eligible)}")
for adapter in eligible:
    print(f"{adapter.adapter_id}\t{adapter.temporal_coverage}")
