"use client";

import { useMemo, useState } from "react";

type Tone = "boundary" | "inside" | "breach";

type Scenario = {
  id: number;
  title: string;
  publisher: string;
  family: "Banking" | "Macro" | "Rates" | "Regulatory";
  boundary: string;
  result: string;
  tone: Tone;
};

const metrics = [
  { value: "7", label: "connected engines", note: "one deterministic flow" },
  { value: "30", label: "official adapters", note: "live-validated" },
  { value: "30", label: "boundary replays", note: "internally reproduced" },
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

const scenarios: Scenario[] = [
  { id: 1, title: "SVB funding boundary", publisher: "SEC", family: "Banking", boundary: "2023-03-08", result: "7-engine flow · later 8-K isolated", tone: "boundary" },
  { id: 2, title: "PacWest funding boundary", publisher: "SEC", family: "Banking", boundary: "2023-05-03", result: "Later filing isolated", tone: "boundary" },
  { id: 3, title: "Western Alliance deposit boundary", publisher: "SEC", family: "Banking", boundary: "2023-05-02", result: "17:08 filing stayed later", tone: "boundary" },
  { id: 4, title: "2022 Q4 GDP revision", publisher: "BEA · ALFRED", family: "Macro", boundary: "2023-02-01", result: "Inside range · evaluation only", tone: "inside" },
  { id: 5, title: "BTFP early growth", publisher: "Federal Reserve", family: "Banking", boundary: "2023-03-25", result: "Inside range · evaluation only", tone: "inside" },
  { id: 6, title: "Payroll release", publisher: "BLS", family: "Macro", boundary: "2023-02-04", result: "Inside range · evaluation only", tone: "inside" },
  { id: 7, title: "FOMC target range", publisher: "Federal Reserve", family: "Rates", boundary: "2023-03-23", result: "Inside range · evaluation only", tone: "inside" },
  { id: 8, title: "CPI release snapshot", publisher: "BLS", family: "Macro", boundary: "2023-02-15", result: "Inside range · evaluation only", tone: "inside" },
  { id: 9, title: "2Y–10Y Treasury curve", publisher: "ALFRED", family: "Rates", boundary: "2023-03-16", result: "+6 bp upper breach", tone: "breach" },
  { id: 10, title: "Treasury cash balance", publisher: "U.S. Treasury", family: "Rates", boundary: "2023-06-01", result: "Inside range · baseline miss visible", tone: "inside" },
  { id: 11, title: "SOFR spike", publisher: "New York Fed", family: "Rates", boundary: "2019-09-17", result: "+282 bp upper breach", tone: "breach" },
  { id: 12, title: "Commercial crude stocks", publisher: "EIA", family: "Macro", boundary: "2020-04-16", result: "+15,022 thousand-barrel breach", tone: "breach" },
  { id: 13, title: "Initial unemployment claims", publisher: "U.S. DOL", family: "Macro", boundary: "2020-03-20", result: "+2,932,000-person breach", tone: "breach" },
  { id: 14, title: "91-day Treasury auction", publisher: "TreasuryDirect", family: "Rates", boundary: "2020-03-18", result: "−19 bp lower breach", tone: "breach" },
  { id: 15, title: "Personal saving rate", publisher: "BEA", family: "Macro", boundary: "2020-04-01", result: "+460 bp upper breach", tone: "breach" },
  { id: 16, title: "Industrial production", publisher: "Federal Reserve", family: "Macro", boundary: "2020-03-18", result: "−600 bp lower breach", tone: "breach" },
  { id: 17, title: "Retail sales", publisher: "U.S. Census", family: "Macro", boundary: "2020-03-18", result: "−740 bp lower breach", tone: "breach" },
  { id: 18, title: "Housing starts", publisher: "Census · HUD", family: "Macro", boundary: "2020-03-19", result: "−383,000-unit breach", tone: "breach" },
  { id: 19, title: "Revolving consumer credit", publisher: "Federal Reserve", family: "Macro", boundary: "2020-04-07", result: "−3,550 bp lower breach", tone: "breach" },
  { id: 20, title: "Construction spending", publisher: "U.S. Census", family: "Macro", boundary: "2020-04-01", result: "−$3,659M lower breach", tone: "breach" },
  { id: 21, title: "Purchase-only house prices", publisher: "FHFA", family: "Macro", boundary: "2020-04-22", result: "−60 bp lower breach", tone: "breach" },
  { id: 22, title: "Durable-goods orders", publisher: "U.S. Census", family: "Macro", boundary: "2020-03-25", result: "−1,560 bp lower breach", tone: "breach" },
  { id: 23, title: "Trade deficit", publisher: "Census · BEA", family: "Macro", boundary: "2020-04-02", result: "+$4,483M upper breach", tone: "breach" },
  { id: 24, title: "New-home sales", publisher: "Census · HUD", family: "Macro", boundary: "2020-03-24", result: "−103,000-unit breach", tone: "breach" },
  { id: 25, title: "Working-gas stocks", publisher: "EIA", family: "Macro", boundary: "2020-03-19", result: "−20 Bcf lower breach", tone: "breach" },
  { id: 26, title: "Producer prices", publisher: "BLS", family: "Macro", boundary: "2020-04-09", result: "−110 bp lower breach", tone: "breach" },
  { id: 27, title: "UST 2Y open interest", publisher: "CFTC", family: "Regulatory", boundary: "2026-07-24", result: "+71,513-contract breach", tone: "breach" },
  { id: 28, title: "Central-bank liquidity swaps", publisher: "Federal Reserve", family: "Banking", boundary: "2020-03-26", result: "Inside range · no success claim", tone: "inside" },
  { id: 29, title: "All-import prices", publisher: "BLS", family: "Macro", boundary: "2020-03-13", result: "−130 bp lower breach", tone: "breach" },
  { id: 30, title: "All-export prices", publisher: "BLS", family: "Macro", boundary: "2020-03-13", result: "Inside range · no success claim", tone: "inside" },
];

const filters = ["All", "Banking", "Macro", "Rates", "Regulatory"] as const;

const artifacts = [
  ["Scale manifest", "c5ba416aa05e…2697", "244 official daily SEC partitions"],
  ["Deep verification", "a1c5ce99c643…0aae", "Every ZIP, CSV, and Parquet partition re-read"],
  ["Query benchmark", "1e9e85a97942…67f1", "Two fresh processes; OS cache uncontrolled"],
  ["Quality receipt", "c14880d7b7f7…acb8", "2,199 clean-subject tests; 2,199 current Python tests"],
  ["Public claim registry", "self-hashed JSON", "5 headline claims · 155 structured claims"],
];

export default function Home() {
  const [family, setFamily] = useState<(typeof filters)[number]>("All");
  const [query, setQuery] = useState("");
  const visibleScenarios = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return scenarios.filter((scenario) => {
      const familyMatches = family === "All" || scenario.family === family;
      const textMatches = !normalized || `${scenario.title} ${scenario.publisher} ${scenario.result}`.toLowerCase().includes(normalized);
      return familyMatches && textMatches;
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
            <a className="primary-action" href="#evidence">Inspect the evidence</a>
            <a className="secondary-action" href="#boundaries">Read the boundaries</a>
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
          <div><dt>Final clean-checkout tests</dt><dd>2,199 / 2,199</dd></div>
          <div><dt>Branch-aware combined coverage</dt><dd>90.48%</dd></div>
          <div><dt>Known dependency findings</dt><dd>0</dd></div>
          <div><dt>Tracked-text scan findings</dt><dd>0</dd></div>
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
          <h2 id="replays-title">Thirty boundaries.<br />Misses stay visible.</h2>
          <p>Inside-range observations are evaluation only. Breaches are never used to widen a range after the fact.</p>
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
            <article className="scenario" key={scenario.id}>
              <div className="scenario-top"><span>#{String(scenario.id).padStart(2, "0")}</span><span className={`tone ${scenario.tone}`}>{scenario.tone === "breach" ? "Visible breach" : scenario.tone === "inside" ? "Evaluation only" : "Boundary proof"}</span></div>
              <h3>{scenario.title}</h3>
              <dl><div><dt>Official source</dt><dd>{scenario.publisher}</dd></div><div><dt>Decision boundary</dt><dd>{scenario.boundary}</dd></div></dl>
              <p>{scenario.result}</p>
            </article>
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
          <p>Public-data cases are not clients. Historical replays are not live trading. Simulated P&amp;L is not investment performance. Tests and hashes establish internal behavior—not source authenticity, deployment, adoption, or impact.</p>
        </div>
      </aside>

      <section className="review-section" id="review" aria-labelledby="review-title">
        <div className="review-copy">
          <span className="section-number">06 / Independent evidence</span>
          <h2 id="review-title">The final gate cannot be self-awarded.</h2>
          <p>A qualified reviewer must independently reproduce a result or review a domain method, identify a real issue, and follow that issue through resolution.</p>
          <p className="archive-digest">The 6.67 MB source archive is bound to commit <code>e150136dc0a2</code> and SHA-256 <code>f7c287c6…065ab</code>. Prior packaged review ZIPs are excluded.</p>
          <div className="review-downloads">
            <a className="primary-action download" href="/review/finreplay-os-e150136.zip" download>Download review source</a>
            <a className="secondary-action download" href="/review/finreplay-review-manifest.json" download>Download manifest</a>
            <a className="secondary-action" href="https://github.com/limingrui679-design/finreplay-os/tree/e150136dc0a2d49d068499ea9fdb01fc4a943a8c">Browse fixed source</a>
          </div>
        </div>
        <ol className="review-steps">
          <li><span>01</span><div><strong>Choose a bounded target</strong><p>One engine method, one replay, or the small committed evidence chain.</p></div></li>
          <li><span>02</span><div><strong>Reproduce independently</strong><p>Use a fresh environment and retain exact commands, revisions, and outputs.</p></div></li>
          <li><span>03</span><div><strong>Record a real issue</strong><p>A failed assumption, unclear boundary, or reproducibility defect—not a courtesy endorsement.</p></div></li>
          <li><span>04</span><div><strong>Close the loop</strong><p>Bind the fix and independent recheck to immutable evidence.</p></div></li>
        </ol>
      </section>

      <footer><span>FinReplay OS · pre-alpha research software</span><span>No investment, legal, accounting, or risk-management advice.</span></footer>
    </main>
  );
}
