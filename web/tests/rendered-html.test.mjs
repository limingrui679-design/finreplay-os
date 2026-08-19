import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

let workerPromise;

function canonicalJson(value) {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

async function worker() {
  if (workerPromise) return workerPromise;
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  workerPromise = import(workerUrl.href).then((module) => module.default);
  return workerPromise;
}

async function render(path = "/") {
  const handler = await worker();
  return handler.fetch(
    new Request(`http://localhost${path}`, {
      headers: { accept: "text/html" },
    }),
    {
      ASSETS: {
        fetch: async () => new Response("Not found", { status: 404 }),
      },
    },
    {
      waitUntil() {},
      passThroughOnException() {},
    },
  );
}

test("server-renders the FinReplay evidence surface", async () => {
  const response = await render();
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type") ?? "", /^text\/html\b/i);

  const html = await response.text();
  assert.match(html, /<title>FinReplay OS · Evidence before confidence<\/title>/i);
  assert.match(html, /Put the market back in time/);
  assert.match(html, /1\.014B/);
  assert.match(html, /2,232 \/ 2,232/);
  assert.match(html, /bb0797da0c48…bfe01/);
  assert.match(html, /Thirty boundaries/);
  assert.match(html, /Evidence-bounded capabilities/);
  assert.match(html, /Choose a capability path/);
  assert.match(html, /The final gate cannot be self-awarded/);
  assert.match(html, /finreplay-os-044661b\.zip/);
  assert.match(html, /380a33d5…57e6f0/);
  assert.match(html, /github\.com\/limingrui679-design\/finreplay-os\/tree\/044661bf0d4d/);
  assert.match(html, /Independent review/);
  assert.doesNotMatch(html, /codex-preview|react-loading-skeleton|Your site is taking shape/i);
});

test("renders all thirty labelled scenario cards", async () => {
  const response = await render();
  const html = await response.text();

  assert.equal((html.match(/class="scenario"/g) ?? []).length, 30);
  assert.equal((html.match(/href="\/replays\//g) ?? []).length, 30);
  assert.equal((html.match(/Visible breach/g) ?? []).length, 19);
  assert.match(html, /Public-data cases are not clients/);
  assert.match(html, /No multiplier/);
});

test("renders a deep-linked replay with claims, hashes, and download", async () => {
  const response = await render("/replays/svb-2023");
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.match(html, /<title>SVB funding boundary · FinReplay OS<\/title>/i);
  assert.match(html, /Seven actual engine implementations ran/);
  assert.match(html, /claim-extracted-seven-engine-pack/);
  assert.match(html, /c62c22dcbd15e29592a10811117a565d2bf9bee34877a4fbcbf24994383efd35/);
  assert.match(html, /\/replaypacks\/svb-2023\.zip/);
  assert.match(html, /Internal reproduction is not external method review/);
  assert.match(html, /Why this case matters/);
  assert.match(html, /Decision-making under risk and constraints/);
});

test("renders every scenario deep link and the documentation route", async () => {
  const explorer = JSON.parse(
    await readFile(new URL("../data/scenarios.json", import.meta.url), "utf8"),
  );
  const scenarios = explorer.scenarios;
  assert.equal(explorer.scenarioCount, 30);
  assert.equal(scenarios.length, 30);

  for (const scenario of scenarios) {
    const response = await render(`/replays/${scenario.slug}`);
    assert.equal(response.status, 200, scenario.slug);
    const html = await response.text();
    assert.match(html, new RegExp(scenario.downloadSha256), scenario.slug);
    assert.match(html, new RegExp(`/replaypacks/${scenario.slug}\\.zip`), scenario.slug);
  }

  const docs = await render("/docs");
  assert.equal(docs.status, 200);
  const docsHtml = await docs.text();
  assert.match(docsHtml, /finreplay demo svb-2023 --offline --open/);
  assert.match(docsHtml, /finreplay capability show decision-risk/);
});

test("ships complete method, dimension, and pathway metadata for every case", async () => {
  const explorer = JSON.parse(
    await readFile(new URL("../data/scenarios.json", import.meta.url), "utf8"),
  );
  const source = await readFile(
    new URL("../../src/finreplay/resources/scenario-explorer.json", import.meta.url),
  );
  const sourceCatalog = JSON.parse(source.toString("utf8"));
  const sourceCatalogHash = sourceCatalog.catalog_sha256;
  delete sourceCatalog.catalog_sha256;
  const lensIds = new Set(explorer.lenses.map((lens) => lens.lensId));
  const represented = new Set(explorer.scenarios.flatMap((scenario) => scenario.lensIds));

  assert.equal(explorer.scenarioCount, 30);
  assert.equal(explorer.lenses.length, 10);
  assert.equal(explorer.pathways.length, 5);
  assert.deepEqual([...represented].sort(), [...lensIds].sort());
  assert.equal(
    createHash("sha256").update(source).digest("hex"),
    explorer.explorerFileSha256,
  );
  assert.equal(
    createHash("sha256").update(canonicalJson(sourceCatalog)).digest("hex"),
    sourceCatalogHash,
  );
  assert.equal(explorer.explorerCatalogSha256, sourceCatalogHash);
  assert.deepEqual(
    explorer.scenarios.map((scenario) => scenario.id),
    Array.from({ length: 30 }, (_, index) => index + 1),
  );
  for (const scenario of explorer.scenarios) {
    assert.ok(scenario.primaryMethod, scenario.slug);
    assert.ok(scenario.decisionQuestion, scenario.slug);
    assert.ok(scenario.lensIds.length >= 2, scenario.slug);
  }
});

test("renders the evidence-bounded capability directory", async () => {
  const response = await render("/capabilities");
  assert.equal(response.status, 200);
  const html = await response.text();

  assert.match(html, /<title>Capability map · FinReplay OS<\/title>/i);
  assert.match(html, /Choose the question/);
  assert.match(html, /Portfolio anatomy/);
  assert.match(html, /Recorded outcome composition/);
  assert.match(html, /Analytical dimension coverage/);
  assert.equal((html.match(/class="pathway-card"/g) ?? []).length, 5);
  assert.equal((html.match(/class="capability-card"/g) ?? []).length, 10);
  assert.match(html, /id="public-policy-evidence"/);
  assert.equal((html.match(/class="pathway-grid"/g) ?? []).length, 1);
  assert.match(html, /Five ways through thirty cases/);
  assert.match(html, /Direct evidence/);
  assert.match(html, /Transferable method/);
  assert.match(html, /Boundary only/);
  assert.match(html, /not evidence of domain deployment/i);
  assert.match(html, /catalog_sha256=/);
});

test("ships capability hash navigation recovery for client-side routes", async () => {
  const source = await readFile(
    new URL("../app/capabilities/hash-target-scroller.tsx", import.meta.url),
    "utf8",
  );

  assert.match(source, /window\.location\.hash/);
  assert.match(source, /scrollIntoView/);
  assert.match(source, /hashchange/);
  assert.match(source, /ResizeObserver/);
});

test("keeps uncurated cases useful without inventing a direct capability route", async () => {
  const response = await render("/replays/btfp-growth-2023");
  const html = await response.text();

  assert.equal(response.status, 200);
  assert.match(html, /Archived-release growth envelope/);
  assert.match(html, /not currently selected as a strongest example/);
  assert.match(html, /not a direct\s+domain-experience claim/);
});

test("ships a self-hashed capability catalog bound to the scenario catalog", async () => {
  const catalog = JSON.parse(
    await readFile(new URL("../data/capabilities.json", import.meta.url), "utf8"),
  );
  const packagedCatalog = JSON.parse(
    await readFile(
      new URL("../../src/finreplay/resources/capability-catalog.json", import.meta.url),
      "utf8",
    ),
  );
  const scenarioCatalog = JSON.parse(
    await readFile(
      new URL("../../src/finreplay/resources/scenario-catalog.json", import.meta.url),
      "utf8",
    ),
  );
  const hashPayload = structuredClone(catalog);
  const claimedHash = hashPayload.catalog_sha256;
  delete hashPayload.catalog_sha256;
  const canonical = canonicalJson(hashPayload);

  assert.deepEqual(catalog, packagedCatalog);
  assert.equal(catalog.capability_count, 10);
  assert.equal(catalog.scenario_catalog_sha256, scenarioCatalog.catalog_sha256);
  assert.equal(createHash("sha256").update(canonical).digest("hex"), claimedHash);
});

test("ships thirty manifest-bound deterministic scenario downloads", async () => {
  const directory = new URL("../dist/client/replaypacks/", import.meta.url);
  const manifest = JSON.parse(await readFile(new URL("manifest.json", directory), "utf8"));
  const files = (await readdir(directory)).filter((name) => name.endsWith(".zip"));

  assert.equal(manifest.scenario_count, 30);
  assert.equal(manifest.bundles.length, 30);
  assert.equal(files.length, 30);
  for (const bundle of manifest.bundles) {
    const archive = await readFile(new URL(`${bundle.slug}.zip`, directory));
    assert.equal(archive.length, bundle.bytes, bundle.slug);
    assert.equal(createHash("sha256").update(archive).digest("hex"), bundle.sha256, bundle.slug);
  }
});

test("ships the exact independent-review source archive", async () => {
  const archive = await readFile(
    new URL("../dist/client/review/finreplay-os-044661b.zip", import.meta.url),
  );
  const manifest = JSON.parse(
    await readFile(
      new URL("../dist/client/review/finreplay-review-manifest.json", import.meta.url),
      "utf8",
    ),
  );

  assert.equal(archive.length, 7_488_050);
  assert.equal(
    createHash("sha256").update(archive).digest("hex"),
    "380a33d52890a09a6f686e3fa5d522d0e2b2e5c9022c7421091e61e26657e6f0",
  );
  assert.equal(manifest.source_archive.bytes, archive.length);
  assert.equal(
    manifest.source_archive.sha256,
    "380a33d52890a09a6f686e3fa5d522d0e2b2e5c9022c7421091e61e26657e6f0",
  );
  assert.equal(manifest.repository_subject_commit, "044661bf0d4d5c0a48582a8dde2f8982053dd0e4");
  assert.equal(manifest.source_archive.zip_entry_count, 1_796);
  assert.equal(manifest.source_archive.tracked_file_count, 1_550);
  assert.equal(manifest.source_archive.unsafe_path_count, 0);
  assert.equal(manifest.source_archive.embedded_prior_review_archive_count, 0);
});
