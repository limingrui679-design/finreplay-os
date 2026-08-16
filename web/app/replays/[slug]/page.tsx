import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import {
  formatBytes,
  getScenario,
  githubFile,
  scenarios,
  toneLabel,
} from "@/lib/scenarios";

type ReplayPageProps = {
  params: Promise<{ slug: string }>;
};

export const dynamicParams = false;

export function generateStaticParams() {
  return scenarios.map((scenario) => ({ slug: scenario.slug }));
}

export async function generateMetadata({ params }: ReplayPageProps): Promise<Metadata> {
  const { slug } = await params;
  const scenario = getScenario(slug);
  if (!scenario) return { title: "Replay not found · FinReplay OS" };
  const title = `${scenario.title} · FinReplay OS`;
  const description = `${scenario.publisher} boundary at ${scenario.decisionTime}. ${scenario.result}.`;
  return {
    title,
    description,
    openGraph: { title, description, images: [] },
    twitter: { title, description, images: [] },
  };
}

export default async function ReplayPage({ params }: ReplayPageProps) {
  const { slug } = await params;
  const scenario = getScenario(slug);
  if (!scenario) notFound();
  const current = scenarios.findIndex((candidate) => candidate.slug === scenario.slug);
  const previous = scenarios[(current - 1 + scenarios.length) % scenarios.length];
  const next = scenarios[(current + 1) % scenarios.length];

  return (
    <main className="subpage">
      <a className="skip-link" href="#claims">Skip to claims</a>
      <header className="site-header">
        <Link className="wordmark" href="/" aria-label="FinReplay OS home">
          <span className="wordmark-mark" aria-hidden="true">FR</span>
          <span>FinReplay OS</span>
        </Link>
        <nav aria-label="Replay navigation">
          <Link href="/#replays">All replays</Link>
          <Link href="/docs">Docs</Link>
          <a href={githubFile(scenario.reportPath)}>Source report</a>
        </nav>
        <span className="read-only">Verified download</span>
      </header>

      <article className="replay-detail">
        <header className="detail-hero">
          <div className="eyebrow"><span /> Replay #{String(scenario.id).padStart(2, "0")} · {scenario.family}</div>
          <div className="detail-title-row">
            <div>
              <h1>{scenario.title}</h1>
              <p>{scenario.fullTitle}</p>
            </div>
            <span className={`tone detail-tone ${scenario.tone}`}>{toneLabel(scenario.tone)}</span>
          </div>
          <p className="detail-result">{scenario.result}</p>
          <div className="detail-actions">
            <a className="primary-action download" href={scenario.downloadPath} download>
              Download ReplayPack · {formatBytes(scenario.downloadBytes)}
            </a>
            <a className="secondary-action download" href="/replaypacks/manifest.json" download>
              Verify against manifest
            </a>
          </div>
        </header>

        <section className="detail-facts" aria-label="Scenario identity">
          <div><span>Official source</span><strong>{scenario.publisher}</strong></div>
          <div><span>Decision time</span><strong>{scenario.decisionTime}</strong></div>
          <div><span>Scenario mode</span><strong>{scenario.mode.replaceAll("_", " ")}</strong></div>
          <div><span>Locked input records</span><strong>{scenario.inputRecords}</strong></div>
          <div><span>Historical source set</span><strong>{scenario.historicalReplayEligible ? "Eligible" : "Not eligible"}</strong></div>
          <div><span>Engine artifacts</span><strong>{Object.keys(scenario.engineCounts).length}</strong></div>
        </section>

        <section className="detail-section" aria-labelledby="engine-title">
          <div className="detail-section-heading">
            <span className="section-number">01 / Executed chain</span>
            <h2 id="engine-title">Engine artifacts</h2>
          </div>
          <div className="engine-chip-list">
            {Object.entries(scenario.engineCounts).map(([engine, count]) => (
              <span key={engine}>{engine} <strong>{count}</strong></span>
            ))}
          </div>
        </section>

        <section className="detail-section" id="claims" aria-labelledby="claims-title">
          <div className="detail-section-heading">
            <span className="section-number">02 / Structured claims</span>
            <h2 id="claims-title">Claims stay attached to boundaries.</h2>
            <p>{scenario.claims.length} claims are copied from the deterministic report, not rewritten for this page.</p>
          </div>
          <div className="claim-list">
            {scenario.claims.map((claim) => (
              <article key={claim.claimId}>
                <div className="claim-meta"><code>{claim.claimId}</code><span>{claim.evidenceClass}</span></div>
                <h3>{claim.statement}</h3>
                <p><strong>Boundary:</strong> {claim.boundary}</p>
                {claim.limitations.map((limitation) => <p className="limitation" key={limitation}>{limitation}</p>)}
              </article>
            ))}
          </div>
        </section>

        <section className="detail-section hash-section" aria-labelledby="hash-title">
          <div className="detail-section-heading">
            <span className="section-number">03 / Verification identity</span>
            <h2 id="hash-title">Follow every digest.</h2>
          </div>
          <dl className="hash-list">
            <div><dt>Replay ID</dt><dd><code>{scenario.replayId}</code></dd></div>
            <div><dt>Trace ID</dt><dd><code>{scenario.traceId}</code></dd></div>
            <div><dt>Pack SHA-256</dt><dd><code>{scenario.packSha256}</code></dd></div>
            <div><dt>ZIP SHA-256</dt><dd><code>{scenario.downloadSha256}</code></dd></div>
            <div><dt>Input-lock SHA-256</dt><dd><code>{scenario.inputLockSha256}</code></dd></div>
            <div><dt>Proof SHA-256</dt><dd><code>{scenario.proofSha256}</code></dd></div>
            <div><dt>Recorded code commit</dt><dd><code>{scenario.codeCommit}</code></dd></div>
          </dl>
        </section>

        <aside className="boundary detail-boundary">
          <span className="boundary-icon" aria-hidden="true">!</span>
          <div><h2>Scenario claim boundary</h2><p>{scenario.claimBoundary}</p></div>
        </aside>

        <section className="source-links" aria-labelledby="source-links-title">
          <h2 id="source-links-title">Repository evidence</h2>
          <a href={githubFile(scenario.documentationPath)}>Scenario documentation ↗</a>
          <a href={githubFile(scenario.proofPath)}>Eight-gate proof JSON ↗</a>
          <a href={githubFile(scenario.reportPath)}>Compiled report JSON ↗</a>
        </section>

        <nav className="replay-pagination" aria-label="Adjacent replays">
          <Link href={`/replays/${previous.slug}`}>← {previous.title}</Link>
          <Link href={`/replays/${next.slug}`}>{next.title} →</Link>
        </nav>
      </article>

      <footer><span>FinReplay OS · deterministic public-data ReplayPack</span><span>Internal reproduction is not external method review.</span></footer>
    </main>
  );
}
