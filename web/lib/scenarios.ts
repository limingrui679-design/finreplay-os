import scenarioData from "@/data/scenarios.json";

export type Tone = "boundary" | "inside" | "breach";
export type ScenarioFamily = "Banking" | "Macro" | "Rates" | "Regulatory";

export type ScenarioClaim = {
  claimId: string;
  statement: string;
  evidenceClass: "observed" | "reported" | "extracted" | "inferred" | "simulated";
  boundary: string;
  limitations: string[];
};

export type Scenario = {
  id: number;
  slug: string;
  title: string;
  fullTitle: string;
  publisher: string;
  family: ScenarioFamily;
  result: string;
  tone: Tone;
  scenarioId: string;
  scenarioVersion: string;
  replayId: string;
  mode: string;
  decisionTime: string;
  decisionDate: string;
  inputRecords: number;
  historicalReplayEligible: boolean;
  codeCommit: string;
  packSha256: string;
  traceId: string;
  proofSha256: string;
  inputLockSha256: string;
  claimBoundary: string;
  engineCounts: Record<string, number>;
  claims: ScenarioClaim[];
  proofPath: string;
  reportPath: string;
  documentationPath: string;
  downloadPath: string;
  downloadBytes: number;
  downloadSha256: string;
};

export const scenarios = scenarioData as Scenario[];
export const filters = ["All", "Banking", "Macro", "Rates", "Regulatory"] as const;

export function getScenario(slug: string): Scenario | undefined {
  return scenarios.find((scenario) => scenario.slug === slug);
}

export function toneLabel(tone: Tone): string {
  if (tone === "breach") return "Visible breach";
  if (tone === "inside") return "Evaluation only";
  return "Boundary proof";
}

export function formatBytes(bytes: number): string {
  return `${(bytes / 1024).toFixed(1)} KiB`;
}

export function githubFile(path: string): string {
  return `https://github.com/limingrui679-design/finreplay-os/blob/main/${path}`;
}
