import type { Metadata } from "next";
import Link from "next/link";

import {
  capabilities,
  capabilityCatalog,
  scopeLabel,
} from "@/lib/capabilities";
import {
  explorer,
  getScenario,
  githubFile,
  lensById,
  lenses,
  pathways,
  scenarios,
} from "@/lib/scenarios";

export const metadata: Metadata = {
  title: "Capability map · FinReplay OS",
  description: "Choose an evidence-bounded route through FinReplay's methods and public-data scenarios.",
};

const scopeDefinitions = [
  ["direct", "Direct evidence", "Implemented code and committed machine evidence support the stated capability."],
  ["transferable", "Transferable method", "The method is demonstrated here; adjacent-domain practice is not."],
  ["boundary_only", "Boundary only", "The cases expose what cannot be inferred rather than proving domain experience."],
] as const;

const outcomeSummary = [
  {
    tone: "boundary",
    label: "Boundary proof",
    count: scenarios.filter((scenario) => scenario.tone === "boundary").length,
  },
  {
    tone: "inside",
    label: "Evaluation only",
    count: scenarios.filter((scenario) => scenario.tone === "inside").length,
  },
  {
    tone: "breach",
    label: "Visible breach",
    count: scenarios.filter((scenario) => scenario.tone === "breach").length,
  },
] as const;

const dimensionSummary = lenses.map((lens) => ({
  ...lens,
  count: scenarios.filter((scenario) => scenario.lensIds.includes(lens.lensId)).length,
}));

export default function CapabilitiesPage() {
  return (
    <main className="subpage capability-page">
      <header className="site-header">
        <Link className="wordmark" href="/" aria-label="FinReplay OS home">
          <span className="wordmark-mark" aria-hidden="true">FR</span>
          <span>FinReplay OS</span>
        </Link>
        <nav aria-label="Capability navigation">
          <Link href="/#replays">Replays</Link>
          <Link href="/docs">Docs</Link>
          <a href={githubFile("docs/capability-map.md")}>Source map</a>
        </nav>
        <span className="read-only">Evidence-bounded</span>
      </header>

      <header className="capability-hero">
        <div className="eyebrow"><span /> Ten analytical routes</div>
        <h1>Choose the question.<br /><em>Keep the boundary.</em></h1>
        <p>
          The same replay can demonstrate several skills, but a transferable method is not a
          domain credential. Every route below names its strongest cases, machine locators, and
          the claims it still cannot support.
        </p>
      </header>

      <section className="scope-legend" aria-labelledby="scope-title">
        <div>
          <span className="section-number">01 / Scope before fit</span>
          <h2 id="scope-title">Three labels prevent one portfolio from pretending to be ten.</h2>
        </div>
        <div className="scope-grid">
          {scopeDefinitions.map(([scope, label, description]) => (
            <article key={scope}>
              <span className={`scope-chip ${scope}`}>{label}</span>
              <p>{description}</p>
            </article>
          ))}
        </div>
      </section>

      <section className="portfolio-anatomy" aria-labelledby="anatomy-title">
        <div className="section-heading">
          <span className="section-number">02 / Portfolio anatomy</span>
          <h2 id="anatomy-title">Coverage overlaps.<br />Outcomes stay visible.</h2>
          <p>
            Capability scope answers how strongly the repository supports a skill. Analytical
            dimensions describe what a case touches. They are deliberately separate: a housing
            tag does not turn a national release into local planning practice.
          </p>
        </div>
        <div className="portfolio-visuals">
          <article className="outcome-visual">
            <header><strong>Recorded outcome composition</strong><small>30 cases · mutually exclusive</small></header>
            <div
              className="outcome-bar"
              role="img"
              aria-label="3 boundary proofs, 8 evaluation-only results, and 19 visible breaches"
            >
              {outcomeSummary.map((outcome) => (
                <span
                  className={`outcome-${outcome.tone}`}
                  key={outcome.tone}
                  style={{ flexGrow: outcome.count }}
                  title={`${outcome.label}: ${outcome.count}`}
                />
              ))}
            </div>
            <dl className="outcome-legend">
              {outcomeSummary.map((outcome) => (
                <div key={outcome.tone}>
                  <dt><span className={`legend-swatch outcome-${outcome.tone}`} />{outcome.label}</dt>
                  <dd>{outcome.count}</dd>
                </div>
              ))}
            </dl>
          </article>
          <article className="dimension-visual">
            <header><strong>Analytical dimension coverage</strong><small>overlapping tags</small></header>
            <div className="dimension-bars">
              {dimensionSummary.map((dimension) => (
                <div className="dimension-row" key={dimension.lensId}>
                  <div><span>{dimension.shortLabel}</span><strong>{dimension.count}</strong></div>
                  <span className="dimension-track" aria-hidden="true">
                    <span style={{ width: `${(dimension.count / scenarios.length) * 100}%` }} />
                  </span>
                </div>
              ))}
            </div>
          </article>
        </div>
      </section>

      <section className="pathway-directory" aria-labelledby="pathway-title">
        <div className="section-heading">
          <span className="section-number">03 / Curated pathways</span>
          <h2 id="pathway-title">Five ways through thirty cases.</h2>
          <p>Each pathway combines distinct sources and mechanisms around one decision problem; it does not create a new result.</p>
        </div>
        <div className="pathway-grid">
          {pathways.map((pathway, index) => (
            <article className="pathway-card" key={pathway.pathwayId}>
              <span className="capability-index">P{String(index + 1).padStart(2, "0")}</span>
              <h3>{pathway.title}</h3>
              <p>{pathway.description}</p>
              <div className="pathway-lenses">
                {pathway.lensIds.map((lensId) => {
                  const lens = lensById.get(lensId);
                  return lens ? <span key={lensId}>{lens.shortLabel}</span> : null;
                })}
              </div>
              <div className="pathway-cases">
                {pathway.scenarioSlugs.map((slug) => {
                  const scenario = getScenario(slug);
                  return scenario ? (
                    <Link href={`/replays/${slug}`} key={slug}>
                      <span>{scenario.title}</span>
                      <small>{scenario.primaryMethod}</small>
                    </Link>
                  ) : null;
                })}
              </div>
            </article>
          ))}
        </div>
        <p className="metadata-boundary">{explorer.claimBoundary}</p>
      </section>

      <section className="capability-directory" aria-labelledby="directory-title">
        <div className="section-heading">
          <span className="section-number">04 / Capability directory</span>
          <h2 id="directory-title">From method to case to evidence.</h2>
          <p>Each path is generated from the same catalog shipped with the Python package and used by the CLI.</p>
        </div>
        <div className="capability-card-grid">
          {capabilities.map((capability, index) => (
            <article className="capability-card" id={capability.capability_id} key={capability.capability_id}>
              <header>
                <span className="capability-index">{String(index + 1).padStart(2, "0")}</span>
                <span className={`scope-chip ${capability.scope}`}>{scopeLabel(capability.scope)}</span>
              </header>
              <h3>{capability.title}</h3>
              <p className="capability-summary">{capability.summary}</p>
              <div className="discipline-list" aria-label="Related disciplines">
                {capability.disciplines.map((discipline) => <span key={discipline}>{discipline}</span>)}
              </div>
              <div className="capability-questions">
                <strong>Questions this route asks</strong>
                <ul>{capability.questions.map((question) => <li key={question}>{question}</li>)}</ul>
              </div>
              <div className="capability-cases">
                <strong>Curated cases</strong>
                <div>
                  {capability.scenario_slugs.map((slug) => {
                    const scenario = getScenario(slug);
                    return scenario ? <Link href={`/replays/${slug}`} key={slug}>{scenario.title}</Link> : null;
                  })}
                </div>
              </div>
              <div className="capability-limits">
                <strong>Still does not prove</strong>
                <ul>{capability.does_not_prove.map((limit) => <li key={limit}>{limit}</li>)}</ul>
              </div>
              <footer className="capability-evidence">
                {capability.evidence_locators.map((locator) => (
                  <a href={githubFile(locator)} key={locator}>{locator.split("/").at(-1)} ↗</a>
                ))}
              </footer>
            </article>
          ))}
        </div>
      </section>

      <aside className="boundary capability-boundary">
        <span className="boundary-icon" aria-hidden="true">!</span>
        <div>
          <h2>Catalog claim boundary</h2>
          <p>{capabilityCatalog.claim_boundary}</p>
          <code>catalog_sha256={capabilityCatalog.catalog_sha256}</code>
        </div>
      </aside>

      <footer><span>FinReplay OS · capability map v1</span><span>Methods transfer; unearned domain claims do not.</span></footer>
    </main>
  );
}
