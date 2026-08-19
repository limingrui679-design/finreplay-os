import capabilityData from "@/data/capabilities.json";

export type CapabilityScope = "direct" | "transferable" | "boundary_only";

export type Capability = {
  capability_id: string;
  title: string;
  short_title: string;
  scope: CapabilityScope;
  summary: string;
  disciplines: string[];
  questions: string[];
  scenario_slugs: string[];
  evidence_locators: string[];
  does_not_prove: string[];
};

export type CapabilityCatalog = {
  schema_version: string;
  catalog_kind: string;
  capability_count: number;
  source_path: string;
  source_sha256: string;
  scenario_catalog_sha256: string;
  claim_boundary: string;
  capabilities: Capability[];
  catalog_sha256: string;
};

export const capabilityCatalog = capabilityData as CapabilityCatalog;
export const capabilities = capabilityCatalog.capabilities;

export function getCapability(capabilityId: string): Capability | undefined {
  return capabilities.find((capability) => capability.capability_id === capabilityId);
}

export function capabilitiesForScenario(slug: string): Capability[] {
  return capabilities.filter((capability) => capability.scenario_slugs.includes(slug));
}

export function scopeLabel(scope: CapabilityScope): string {
  if (scope === "boundary_only") return "Boundary only";
  if (scope === "transferable") return "Transferable method";
  return "Direct evidence";
}
