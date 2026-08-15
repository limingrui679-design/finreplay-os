import assert from "node:assert/strict";
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
