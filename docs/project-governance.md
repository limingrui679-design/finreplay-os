# Project audience, contribution, and tool-assistance boundary

FinReplay OS is pre-alpha research software for quantitative researchers, risk and data analysts,
data engineers, and technical reviewers who need to inspect what was knowable at a historical
decision boundary and reproduce the resulting evidence package. It is not a live-trading,
fiduciary, compliance, accounting, or institutional risk system.

## Recorded contribution boundary

`CITATION.cff`, package metadata, and the repository's maintainer-authored history identify Mingrui
Li as the project author and maintainer. Automated release commits and dependency-bot commits are
separately attributable in Git history. Those records establish repository attribution; they do
not prove that every line was written without tools, that every method is externally correct, or
that an organization adopted the software.

Development has used AI-assisted implementation, review, and documentation in a user-controlled
workspace. The maintainer chooses what to accept and publish and is responsible for the public
claims and releases. AI-produced code, maintainer-run tests, hashes, and automated reviews remain
internal evidence. They do not count as an independent reviewer, a domain expert endorsement, or
real-user validation.

## Evidence and release responsibility

- Every counted public capability must resolve to code, a committed artifact, or a reproducible
  command.
- Public-data scenarios are not client engagements, and simulated outcomes are not realized
  performance.
- The exact Python development environment is pinned in `requirements-dev.lock`; its hash records
  the dependency state, not the authenticity or correctness of upstream packages.
- A public deployment or GitHub release proves bounded availability at a recorded time, not an
  uptime commitment, adoption, or impact.
- Only a qualifying third-party record under the independent-review protocol can change the
  project's external-review state.

When describing FinReplay in a portfolio, CV, or application, use the narrower of the implemented
evidence and the verified public state. Do not convert repository attribution into unaided
authorship, or internal reproducibility into external validation.
