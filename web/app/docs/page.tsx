import type { Metadata } from "next";
import Link from "next/link";

export const metadata: Metadata = {
  title: "Run FinReplay OS · Documentation",
  description: "Install FinReplay OS and reproduce a byte-locked scenario offline in three minutes.",
  openGraph: { images: [] },
  twitter: { images: [] },
};

const groups = [
  ["Adapters", "finreplay adapter list|show|fetch|validate", "Current live validation and explicit temporal eligibility."],
  ["Scenarios", "finreplay scenario list|show|run|verify", "Thirty bundled runners with recorded pack identities."],
  ["ReplayPacks", "finreplay replaypack build|verify|open", "Deterministic portable reports and mutation checks."],
  ["Evidence", "finreplay evidence verify --all-scenarios", "Catalog validation and optional full offline reproduction."],
];

export default function DocsPage() {
  return (
    <main className="subpage docs-page">
      <header className="site-header">
        <Link className="wordmark" href="/" aria-label="FinReplay OS home">
          <span className="wordmark-mark" aria-hidden="true">FR</span>
          <span>FinReplay OS</span>
        </Link>
        <nav aria-label="Documentation navigation">
          <Link href="/#replays">Replays</Link>
          <a href="https://github.com/limingrui679-design/finreplay-os">GitHub</a>
        </nav>
        <span className="read-only">Local-first</span>
      </header>

      <header className="docs-hero">
        <div className="eyebrow"><span /> Three-minute quickstart</div>
        <h1>One command.<br /><em>Zero network inputs.</em></h1>
        <p>Install the alpha package from source, reproduce the flagship seven-engine scenario, and inspect the verified report locally.</p>
      </header>

      <section className="command-panel" aria-labelledby="quickstart-title">
        <div>
          <span className="section-number">01 / Install and run</span>
          <h2 id="quickstart-title">SVB 2023 offline demo</h2>
          <p>The runner uses the byte-locked input set counted by the repository proof. It creates HTML, JSON, Markdown, checksums, a manifest, and a deterministic ZIP.</p>
        </div>
        <pre><code>{`git clone https://github.com/limingrui679-design/finreplay-os.git
cd finreplay-os
python3.12 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/finreplay demo svb-2023 --offline --open`}</code></pre>
      </section>

      <section className="docs-section" aria-labelledby="verify-title">
        <div className="detail-section-heading">
          <span className="section-number">02 / Recheck identity</span>
          <h2 id="verify-title">Reproduce, then compare.</h2>
          <p>The verification command builds in a temporary directory and compares the resulting pack SHA-256 with the recorded catalog value.</p>
        </div>
        <pre><code>{`finreplay scenario verify svb-2023
finreplay evidence verify --all-scenarios`}</code></pre>
      </section>

      <section className="docs-section" aria-labelledby="cli-title">
        <div className="detail-section-heading">
          <span className="section-number">03 / Discoverable surface</span>
          <h2 id="cli-title">CLI map</h2>
        </div>
        <div className="cli-grid">
          {groups.map(([name, command, description]) => (
            <article key={name}><h3>{name}</h3><code>{command}</code><p>{description}</p></article>
          ))}
        </div>
      </section>

      <aside className="boundary detail-boundary">
        <span className="boundary-icon" aria-hidden="true">!</span>
        <div><h2>Temporal boundary</h2><p>A currently validated response may still be `latest_only`. A historical observation date is not proof that the exact retrieved value was knowable then.</p></div>
      </aside>

      <section className="source-links docs-links" aria-labelledby="guides-title">
        <h2 id="guides-title">Continue in the repository</h2>
        <a href="https://github.com/limingrui679-design/finreplay-os/blob/main/docs/quickstart.md">Complete quickstart ↗</a>
        <a href="https://github.com/limingrui679-design/finreplay-os/blob/main/docs/catalog-matrix.md">Adapter and scenario matrix ↗</a>
        <a href="https://github.com/limingrui679-design/finreplay-os/blob/main/docs/adapter-authoring.md">Adapter authoring guide ↗</a>
        <a href="https://github.com/limingrui679-design/finreplay-os/blob/main/docs/scenario-authoring.md">Scenario authoring guide ↗</a>
      </section>

      <footer><span>FinReplay OS · 0.1 alpha</span><span>Research software; no investment advice.</span></footer>
    </main>
  );
}
