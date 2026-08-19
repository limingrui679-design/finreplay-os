"use client";

import Link from "next/link";
import { useMemo, useState } from "react";

import { capabilities, getCapability, scopeLabel } from "@/lib/capabilities";
import { filters, lensById, scenarios, toneLabel } from "@/lib/scenarios";

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
  ["Quality receipt", "bb0797da0c48…bfe01", "2,232 clean-subject tests in the recorded receipt"],
  ["Public pack manifest", "a549ebb5d997…7494", "30 downloadable deterministic ReplayPacks"],
];

const featuredCapabilityIds = [
  "point-in-time-data",
  "statistical-falsification",
  "decision-risk",
  "model-governance",
  "public-policy-evidence",
  "population-place-boundaries",
];

const featuredCapabilities = featuredCapabilityIds.flatMap((capabilityId) => {
  const capability = getCapability(capabilityId);
  return capability ? [capability] : [];
});

export default function Home() {
  const [family, setFamily] = useState<(typeof filters)[number]>("All");
  const [capabilityId, setCapabilityId] = useState("all");
  const [query, setQuery] = useState("");
  const selectedCapability = capabilityId === "all" ? undefined : getCapability(capabilityId);
  const visibleScenarios = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return scenarios.filter((scenario) => {
      const familyMatches = family === "All" || scenario.family === family;
      const capabilityMatches =
        !selectedCapability || selectedCapability.scenario_slugs.includes(scenario.slug);
      const scenarioCapabilities = capabilities
        .filter((capability) => capability.scenario_slugs.includes(scenario.slug))
        .map((capability) => `${capability.title} ${capability.disciplines.join(" ")}`)
        .join(" ");
      const scenarioDimensions = scenario.lensIds
        .map((lensId) => lensById.get(lensId)?.label ?? "")
        .join(" ");
      const haystack = `${scenario.title} ${scenario.publisher} ${scenario.result} ${scenario.primaryMethod} ${scenario.decisionQuestion} ${scenarioDimensions} ${scenarioCapabilities}`.toLowerCase();
      return familyMatches && capabilityMatches && (!normalized || haystack.includes(normalized));
    });
  }, [family, query, selectedCapability]);

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
          <Link href="/capabilities">Capabilities</Link>
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
            <Link className="secondary-action" href="/capabilities">Choose a capability path</Link>
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
          <div><dt>Recorded clean-checkout tests</dt><dd>2,232 / 2,232</dd></div>
          <div><dt>Branch-aware combined coverage</dt><dd>90.35%</dd></div>
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

      <section className="capability-preview" aria-labelledby="capability-preview-title">
        <div className="section-heading">
          <span className="section-number">03 / Evidence-bounded capabilities</span>
          <h2 id="capability-preview-title">One system.<br />Different questions.</h2>
          <p>
            Explore direct engineering evidence, transferable analytical methods, and cases that
            prove only an inference boundary. The labels prevent adjacent-domain relevance from
            becoming an unsupported experience claim.
          </p>
          <Link className="text-action" href="/capabilities">Open all ten capability paths →</Link>
        </div>
        <div className="capability-preview-grid">
          {featuredCapabilities.map((capability) => (
            <Link href={`/capabilities#${capability.capability_id}`} key={capability.capability_id}>
              <span className={`scope-chip ${capability.scope}`}>{scopeLabel(capability.scope)}</span>
              <h3>{capability.title}</h3>
              <p>{capability.summary}</p>
              <small>{capability.scenario_slugs.length} curated cases</small>
            </Link>
          ))}
        </div>
      </section>

      <section className="replay-section" id="replays" aria-labelledby="replays-title">
        <div className="section-heading replay-heading">
          <span className="section-number">04 / Replay ledger</span>
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
          <label className="capability-select">
            <span>Capability path</span>
            <select value={capabilityId} onChange={(event) => setCapabilityId(event.target.value)}>
              <option value="all">All capability paths</option>
              {capabilities.map((capability) => (
                <option value={capability.capability_id} key={capability.capability_id}>
                  {capability.short_title} · {scopeLabel(capability.scope)}
                </option>
              ))}
            </select>
          </label>
        </div>
        {selectedCapability && (
          <p className="selected-capability-note">
            <strong>{scopeLabel(selectedCapability.scope)}:</strong> {selectedCapability.summary}
          </p>
        )}
        <p className="result-count" aria-live="polite">Showing {visibleScenarios.length} of 30 replay-proven boundaries</p>
        <div className="scenario-grid">
          {visibleScenarios.map((scenario) => (
            <Link className="scenario" href={`/replays/${scenario.slug}`} key={scenario.id}>
              <div className="scenario-top">
                <span>#{String(scenario.id).padStart(2, "0")}</span>
                <span className={`tone ${scenario.tone}`}>{toneLabel(scenario.tone)}</span>
              </div>
              <h3>{scenario.title}</h3>
              <small className="scenario-method">{scenario.primaryMethod}</small>
              <div className="scenario-dimensions" aria-label="Analytical dimensions">
                {scenario.lensIds.slice(0, 3).map((lensId) => {
                  const lens = lensById.get(lensId);
                  return lens ? <span key={lensId}>{lens.shortLabel}</span> : null;
                })}
                {scenario.lensIds.length > 3 && <span>+{scenario.lensIds.length - 3}</span>}
              </div>
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
          <span className="section-number">05 / Measured scale</span>
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
          <span className="section-number">06 / Machine locators</span>
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
          <span className="section-number">07 / Independent evidence</span>
          <h2 id="review-title">The final gate cannot be self-awarded.</h2>
          <p>A qualified reviewer must independently reproduce a result or review a domain method, identify a real issue, and follow that issue through resolution.</p>
          <p className="archive-digest">The fixed review snapshot is bound to commit <code>18087f8fe4f6</code> and SHA-256 <code>f18290ad…38eab5</code>. The 30 current per-scenario downloads are separately self-hashed.</p>
          <div className="review-downloads">
            <a className="primary-action download" href="/review/finreplay-os-18087f8.zip" download>Download review source</a>
            <a className="secondary-action download" href="/replaypacks/manifest.json" download>Download pack manifest</a>
            <a className="secondary-action" href="https://github.com/limingrui679-design/finreplay-os/tree/18087f8fe4f6b08885e75ed481d21f115f8e1ab4">Browse fixed source</a>
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

      <footer><span>FinReplay OS · 0.2 alpha research software</span><span>No investment, legal, accounting, or risk-management advice.</span></footer>
    </main>
  );
}
