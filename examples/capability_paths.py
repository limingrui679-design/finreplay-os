"""List curated scenarios without dropping capability scope or negative boundaries."""

from finreplay.catalog import load_capability_catalog

catalog = load_capability_catalog()

for capability in catalog.capabilities:
    scenarios = ", ".join(capability.scenario_slugs)
    print(f"{capability.capability_id}\t{capability.scope}\t{scenarios}")
    for boundary in capability.does_not_prove:
        print(f"  does_not_prove={boundary}")
