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
  assert.match(html, /2,197 \/ 2,197/);
  assert.match(html, /Thirty boundaries/);
  assert.match(html, /The final gate cannot be self-awarded/);
  assert.match(html, /finreplay-os-62bf793d017b\.zip/);
  assert.match(html, /781df836…66a418/);
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
    new URL("../dist/client/review/finreplay-os-62bf793d017b.zip", import.meta.url),
  );
  const manifest = JSON.parse(
    await readFile(
      new URL("../dist/client/review/finreplay-review-manifest.json", import.meta.url),
      "utf8",
    ),
  );

  assert.equal(archive.length, 6_662_710);
  assert.equal(
    createHash("sha256").update(archive).digest("hex"),
    "781df836758a84a37ee65cd76fcb1bfd185e32ebef36bda566cea5c1c566a418",
  );
  assert.equal(manifest.source_archive.bytes, archive.length);
  assert.equal(
    manifest.source_archive.sha256,
    "781df836758a84a37ee65cd76fcb1bfd185e32ebef36bda566cea5c1c566a418",
  );
});
