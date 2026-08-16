import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile, readdir } from "node:fs/promises";
import test from "node:test";

let workerPromise;

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
  assert.match(html, /2,212 \/ 2,212/);
  assert.match(html, /Thirty boundaries/);
  assert.match(html, /The final gate cannot be self-awarded/);
  assert.match(html, /finreplay-os-e150136\.zip/);
  assert.match(html, /f7c287c6…065ab/);
  assert.match(html, /github\.com\/limingrui679-design\/finreplay-os\/tree\/e150136dc0a2/);
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
});

test("renders every scenario deep link and the documentation route", async () => {
  const scenarios = JSON.parse(
    await readFile(new URL("../data/scenarios.json", import.meta.url), "utf8"),
  );
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
  assert.match(await docs.text(), /finreplay demo svb-2023 --offline --open/);
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
    new URL("../dist/client/review/finreplay-os-e150136.zip", import.meta.url),
  );
  const manifest = JSON.parse(
    await readFile(
      new URL("../dist/client/review/finreplay-review-manifest.json", import.meta.url),
      "utf8",
    ),
  );

  assert.equal(archive.length, 6_673_935);
  assert.equal(
    createHash("sha256").update(archive).digest("hex"),
    "f7c287c613b366f38705c1eaf5a21971ff3a5380f11c5fc4a0e1b44900a065ab",
  );
  assert.equal(manifest.source_archive.bytes, archive.length);
  assert.equal(
    manifest.source_archive.sha256,
    "f7c287c613b366f38705c1eaf5a21971ff3a5380f11c5fc4a0e1b44900a065ab",
  );
  assert.equal(manifest.source_archive.embedded_prior_review_archive_count, 0);
});
