import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";

async function render() {
  const workerUrl = new URL("../dist/server/index.js", import.meta.url);
  workerUrl.searchParams.set("test", `${process.pid}-${Date.now()}`);
  const { default: worker } = await import(workerUrl.href);

  return worker.fetch(
    new Request("http://localhost/", {
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
  assert.match(html, /2,199 \/ 2,199/);
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
  assert.equal((html.match(/Visible breach/g) ?? []).length, 19);
  assert.match(html, /Public-data cases are not clients/);
  assert.match(html, /No multiplier/);
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
