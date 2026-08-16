# Evidence labels

FinReplay keeps evidence classes distinct so a calculation cannot silently become an observation.

| Label | Meaning | Example |
|---|---|---|
| `observed` | Directly observed in the identified source artifact | A published table cell |
| `reported` | A statement attributed to the identified publisher | A release headline |
| `extracted` | Deterministically parsed from a source artifact | A normalized PDF table value |
| `inferred` | Derived from disclosed facts and rules | A relationship inferred from filings |
| `simulated` | Produced by a model or stress program | A bounded shock outcome |

These labels answer “how did this value enter the evidence chain?” They do not by themselves
establish temporal eligibility, source authenticity, or methodological validity.

## Time fields

- **Economic time** identifies the period or event the value describes.
- **Knowledge time** identifies when the exact value was defensibly available to the decision
  maker.
- **Retrieval time** records when FinReplay obtained the response.

An old economic date in a current response does not create an old knowledge time. When the
publication instant cannot be established, the scenario must apply and disclose a conservative
bound or reject the value.

## Artifact status

An artifact can be decision-time eligible, evaluation-only, unavailable, or excluded under the
repository's typed contracts. Evaluation-only later outcomes must never flow back into a
decision-time feature set.

## Public wording

Prefer narrow, evidenced verbs: “the offline runner reproduced the recorded pack hash” or “the
adapter validated a current official response at the recorded retrieval time.” Avoid converting
those facts into claims of external validation, deployment, adoption, financial performance, or
impact.
