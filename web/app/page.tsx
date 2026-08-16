"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { filters, scenarios, toneLabel } from "@/lib/scenarios";

const metrics = [
  { value: "7", label: "connected engines", note: "one deterministic flow" },
  { value: "30", label: "official adapters", note: "live-validated" },
  { value: "30", label: "boundary replays", note: "offline runners + downloads" },
  { value: "1.014B", label: "physical SEC rows", note: "actually processed" },
];

const engines = [
  ["01", "TimeVault", "Reconstructs what was knowable then; later revisions stay later."],
  ["02", "TrialCourt", "Keeps every attempt and attacks leakage, multiplicity, and regime fragility."],
  ["03", "MarketTwin", "Builds evidence-graded temporal institution and security graphs."],
  ["04", "ShockCompiler", "Separates observed reconstruction, bounds, counterfactuals, and adversarial grids."],
  ["05", "ExecutionLab", "Models non-zero costs, latency, queue uncertainty, liquidity, and capacity."],
  ["06", "CapitalAllocator", "Preserves constraints, infeasibility, reversals, and value of information."],
  ["07", "ReplayStudio", "Exports deterministic, evidence-labelled human and machine reports."],
];

const artifacts = [
  ["Scale manifest", "c5ba416aa05e…2697", "244 official daily SEC partitions"],
  ["Deep verification", "a1c5ce99c643…0aae", "Every ZIP, CSV, and Parquet partition re-read"],
  ["Query benchmark", "1e9e85a97942…67f1", "Two fresh processes; OS cache uncontrolled"],
  ["Quality receipt", "888e6c2a9f27…0f1e", "2,212 clean-subject tests in the recorded receipt"],
  ["Public pack manifest", "a549ebb5d997…7494", "30 downloadable deterministic ReplayPacks"],
];

export default function Home() {
  const [family, setFamily] = useState<(typeof filters)[number]>("All");
  const [query, setQuery] = useState("");
  const visibleScenarios = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return scenarios.filter((scenario) => {
      const familyMatches = family === "All" || scenario.family === family;
      const haystack = `${scenario.title} ${scenario.publisher} ${scenario.result}`.toLowerCase();
      return familyMatches && (!normalized || haystack.includes(normalized));
    });
  }, [family, query]);

  return (
    <main id="main">
      <a className="skip-link" href="#evidence">Skip to evidence</a>
      <header className="site-header">
        <a className="wordmark" href="#top" aria-label="FinReplay OS home">
          <span className="wordmark-mark" aria-hidden="true">FR</span>
          <span>FinReplay OS</span>
        </a>
        <nav aria-label="Primary navigation">
          <a href="#engines">Engines</a>
          <a href="#replays">Replays</a>
          <a href="#scale">Scale</a>
          <Link href="/docs">Docs</Link>
          <a href="#review">Review</a>
        </nav>
        <span className="read-only">Public read-only evidence</span>
      </header>

      <section className="hero" id="top" aria-labelledby="hero-title">
        <div className="eyebrow"><span /> Point-in-time research infrastructure</div>
        <h1 id="hero-title">
          Put the market back in time.<br />
          <em>Put every claim on trial.</em>
        </h1>
        <div className="hero-bottom">
          <p className="hero-copy">
            FinReplay reconstructs what was actually knowable at a decision boundary,
            preserves revisions, attacks research claims, and exposes every assumption
            before capital allocation is even considered.
          </p>
          <div className="hero-actions">
            <a className="primary-action" href="#replays">Explore 30 replays</a>
            <Link className="secondary-action" href="/docs">Run the offline demo</Link>
          </div>
        </div>
      </section>

      <section className="metric-grid" aria-label="Current repository evidence">
        {metrics.map((metric) => (
          <article className="metric" key={metric.label}>
            <strong>{metric.value}</strong>
            <span>{metric.label}</span>
            <small>{metric.note}</small>
          </article>
        ))}
      </section>

      <section className="evidence-band" id="evidence" aria-labelledby="evidence-title">
        <div>
          <span className="section-number">01 / Evidence state</span>
          <h2 id="evidence-title">Internally proven.<br /><em>Externally unfinished.</em></h2>
        </div>
        <dl>
          <div><dt>Recorded clean-checkout tests</dt><dd>2,212 / 2,212</dd></div>
          <div><dt>Branch-aware combined coverage</dt><dd>90.18%</dd></div>
          <div><dt>Known dependency findings</dt><dd>0</dd></div>
          <div><dt>Downloadable scenario packs</dt><dd>30 / 30</dd></div>
          <div><dt>Independent review</dt><dd className="pending">Pending</dd></div>
        </dl>
      </section>

      <section className="engines-section" id="engines" aria-labelledby="engines-title">
        <div className="section-heading">
          <span className="section-number">02 / Connected system</span>
          <h2 id="engines-title">Seven engines.<br />One evidence chain.</h2>
          <p>Each engine emits a typed artifact. Unsupported precision fails closed instead of becoming a polished number.</p>
        </div>
        <div className="engine-list">
          {engines.map(([index, name, description]) => (
            <article className="engine" key={name}>
              <span>{index}</span>
              <h3>{name}</h3>
              <p>{description}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="replay-section" id="replays" aria-labelledby="replays-title">
        <div className="section-heading replay-heading">
          <span className="section-number">03 / Replay ledger</span>
          <h2 id="replays-title">Thirty boundaries.<br />Every pack opens.</h2>
          <p>Each card now has a stable evidence page, explicit hashes, structured claims, and a deterministic ReplayPack download.</p>
        </div>
        <div className="replay-tools">
          <div className="filters" aria-label="Filter scenarios by family">
            {filters.map((item) => (
              <button key={item} type="button" aria-pressed={family === item} onClick={() => setFamily(item)}>{item}</button>
            ))}
          </div>
          <label className="search-label">
            <span>Search replay ledger</span>
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Publisher, event, outcome…" />
          </label>
        </div>
        <p className="result-count" aria-live="polite">Showing {visibleScenarios.length} of 30 replay-proven boundaries</p>
        <div className="scenario-grid">
          {visibleScenarios.map((scenario) => (
            <Link className="scenario" href={`/replays/${scenario.slug}`} key={scenario.id}>
              <div className="scenario-top">
                <span>#{String(scenario.id).padStart(2, "0")}</span>
                <span className={`tone ${scenario.tone}`}>{toneLabel(scenario.tone)}</span>
              </div>
              <h3>{scenario.title}</h3>
              <dl>
                <div><dt>Official source</dt><dd>{scenario.publisher}</dd></div>
                <div><dt>Decision boundary</dt><dd>{scenario.decisionDate}</dd></div>
              </dl>
              <p>{scenario.result}<span className="scenario-arrow" aria-hidden="true"> ↗</span></p>
            </Link>
          ))}
        </div>
        {visibleScenarios.length === 0 && <p className="empty-state">No replay matches this filter. Clear the search or choose another family.</p>}
      </section>

      <section className="scale-section" id="scale" aria-labelledby="scale-title">
        <div className="section-heading light">
          <span className="section-number">04 / Measured scale</span>
          <h2 id="scale-title">No multiplier.<br />No estimate.</h2>
          <p>Exactly 1,014,736,394 physical CSV rows from 244 continuous official SEC daily archives were processed into 12,277,974,518 Parquet bytes.</p>
        </div>
        <div className="scale-proof">
          <article><span>Fresh process 01</span><strong>55.878863s</strong><small>all rows scanned and input bytes hash-verified</small></article>
          <article><span>Fresh process 02</span><strong>55.944395s</strong><small>distinct cutoff; OS cache explicitly uncontrolled</small></article>
          <article><span>Deep pass</span><strong>1,404.358697s</strong><small>every ZIP, CSV, and Parquet partition re-read</small></article>
        </div>
      </section>

      <section className="artifact-section" aria-labelledby="artifact-title">
        <div className="section-heading compact">
          <span className="section-number">05 / Machine locators</span>
          <h2 id="artifact-title">Follow the hashes.</h2>
        </div>
        <div className="artifact-list">
          {artifacts.map(([name, digest, description]) => (
            <article key={name}><div><h3>{name}</h3><p>{description}</p></div><code>{digest}</code></article>
          ))}
        </div>
      </section>

      <aside className="boundary" id="boundaries" aria-labelledby="boundary-title">
        <span className="boundary-icon" aria-hidden="true">!</span>
        <div>
          <h2 id="boundary-title">What this evidence does not prove</h2>
          <p>Public-data cases are not clients. Historical replays are not live trading. Simulated P&amp;L is not investment performance. Tests and hashes establish internal behavior—not source authenticity, external method review, adoption, or impact.</p>
        </div>
      </aside>

      <section className="review-section" id="review" aria-labelledby="review-title">
        <div className="review-copy">
          <span className="section-number">06 / Independent evidence</span>
          <h2 id="review-title">The final gate cannot be self-awarded.</h2>
          <p>A qualified reviewer must independently reproduce a result or review a domain method, identify a real issue, and follow that issue through resolution.</p>
          <p className="archive-digest">The fixed review snapshot is bound to commit <code>e150136dc0a2</code> and SHA-256 <code>f7c287c6…065ab</code>. The 30 current per-scenario downloads are separately self-hashed.</p>
          <div className="review-downloads">
            <a className="primary-action download" href="/review/finreplay-os-e150136.zip" download>Download review source</a>
            <a className="secondary-action download" href="/replaypacks/manifest.json" download>Download pack manifest</a>
            <a className="secondary-action" href="https://github.com/limingrui679-design/finreplay-os/tree/e150136dc0a2d49d068499ea9fdb01fc4a943a8c">Browse fixed source</a>
            <a className="secondary-action" href="https://github.com/limingrui679-design/finreplay-os/issues/new?template=independent-review.yml">Start independent review</a>
          </div>
        </div>
        <ol className="review-steps">
          <li><span>01</span><div><strong>Choose a bounded target</strong><p>One engine method, one replay, or the small committed evidence chain.</p></div></li>
          <li><span>02</span><div><strong>Reproduce independently</strong><p>Use a fresh environment and retain exact commands, revisions, and outputs.</p></div></li>
          <li><span>03</span><div><strong>Record a real issue</strong><p>A failed assumption, unclear boundary, or reproducibility defect—not a courtesy endorsement.</p></div></li>
          <li><span>04</span><div><strong>Close the loop</strong><p>Bind the fix and independent recheck to immutable evidence.</p></div></li>
        </ol>
      </section>

      <footer><span>FinReplay OS · 0.1 alpha research software</span><span>No investment, legal, accounting, or risk-management advice.</span></footer>
    </main>
  );
}
