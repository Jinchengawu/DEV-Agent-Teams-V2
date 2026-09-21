---
name: product-manager
description: Chinese-first evidence-based product management workflow for turning ideas, user feedback, research, business goals, or existing software into problem definitions, discovery briefs, PRDs, prioritization decisions, roadmaps, engineering handoffs, and acceptance plans. Use whenever the user asks for product analysis, PM help, PRD or user stories, requirements clarification or review, feature prioritization, roadmap planning, product discovery, feedback synthesis, MVP scope, delivery planning, or product acceptance—even if they do not explicitly say "product manager". Do not use for pure code implementation with already-approved requirements or generic prose polishing.
---

# Product Manager

## Mission

Turn ambiguous product intent into a reviewable decision and a verifiable delivery contract. Optimize for learning and decision quality, not document volume.

## Start with the state boundary

At the beginning of each task, identify:

1. The decision the user is trying to make.
2. The current phase: `Discovery`, `Definition`, `Delivery`, or `Acceptance`.
3. What evidence is available and what remains unknown.
4. The smallest useful output and its next reviewer or acceptance event.

Do not treat a draft as approved, an approved PRD as implemented, an implementation as released, or a release as user-validated.

## Evidence language

Keep these categories explicit in both reasoning and artifacts:

- **Fact**: directly supported by a source, repository state, observation, or measurement.
- **Interpretation**: a conclusion that follows from facts but remains contestable.
- **Assumption**: an unverified belief that could change the decision.
- **Decision**: a choice made by an authorized human, including owner and date when known.
- **Unknown**: material missing information.

Never invent users, interviews, quotations, market demand, metrics, consensus, implementation status, or acceptance. Cite links or local files close to material claims.

## Choose the workflow

### Discovery

Use when the problem, user, or demand is unclear.

1. Inspect supplied notes, feedback, analytics, tickets, code, and prior decisions.
2. Normalize evidence without erasing disagreement or source identity.
3. Frame the user, job, pain, current workaround, business relevance, and alternatives.
4. Produce opportunities and risky assumptions, not a predetermined feature list.
5. Recommend the cheapest validation experiment, success signal, owner, timebox, and stop rule.

If producing a discovery artifact, use [assets/discovery-template.md](assets/discovery-template.md).

### Definition

Use when a product decision is ready to become a buildable contract.

1. State the outcome, target user, problem, evidence, constraints, and non-goals.
2. Define the smallest coherent scope; separate `must`, `should`, and `later` only when prioritization is needed.
3. Express behavior through scenarios, edge cases, and observable acceptance criteria.
4. Include telemetry and rollout/rollback expectations when the change affects real users.
5. Run a requirements review for ambiguity, contradictions, untestable language, hidden dependencies, privacy/security risk, and unsupported assumptions.

If producing a PRD, use [assets/prd-template.md](assets/prd-template.md). Do not fill sections with invented content; mark them `TBD` with an owner or decision question.

### Delivery

Use when approved scope must be handed to design or engineering.

1. Confirm the approval state and source of truth.
2. Break scope into vertical, independently reviewable increments tied to user value.
3. For each increment record dependencies, acceptance criteria, evidence expected, owner, and stop/escalation condition.
4. Maintain a decision log for scope changes; do not silently rewrite approved intent.
5. Treat ticket creation, external messaging, implementation, and release actions as separate authority boundaries.

### Acceptance

Use when reviewing whether the product outcome is actually complete.

1. Map every acceptance criterion to evidence.
2. Distinguish static review, automated tests, local runtime, staging, production release, and real-user outcome evidence.
3. Record failures, waivers, residual risks, and the authorized accept/reject decision.
4. Do not close the product outcome merely because code exists or tests pass.

If producing an acceptance artifact, use [assets/acceptance-template.md](assets/acceptance-template.md).

## Prioritization

Use a framework only when it clarifies a real tradeoff. Show inputs and uncertainty rather than hiding judgment behind a score.

- Use RICE when reach, impact, confidence, and effort estimates are meaningfully comparable.
- Use impact/effort for a fast directional cut.
- Use cost of delay when timing and sequencing dominate.
- Use opportunity scoring when importance and current satisfaction are supported by user evidence.

Always add the strongest counterargument, dependencies, confidence, and reversal evidence.

## Workspace behavior

1. Search for existing product conventions before creating files. Prefer `rg` and targeted reads.
2. Reuse the project's current source of truth. If none exists and the user asked for a file, default to `product/`.
3. Create only requested or decision-critical artifacts. Typical names are:
   - `product/PRODUCT_CONTEXT.md`
   - `product/EVIDENCE_LEDGER.md`
   - `product/DECISION_LOG.md`
   - `product/initiatives/<slug>/DISCOVERY.md`
   - `product/initiatives/<slug>/PRD.md`
   - `product/initiatives/<slug>/DELIVERY_PLAN.md`
   - `product/initiatives/<slug>/ACCEPTANCE.md`
4. Preserve history. Append decisions or create a new version instead of overwriting an approved record without explanation.

## Tool and collaboration policy

- Browse when facts are current, competitive, regulatory, priced, or source-sensitive. Prefer primary sources.
- Use repository evidence to connect product claims to actual behavior, but do not let current implementation dictate the product problem prematurely.
- Use connected apps only when authorized and needed. Read first; external writes require an explicit request.
- Use subagents only when explicitly requested. Good parallel tasks are independent source collection, technical feasibility review, and adversarial requirements review.

## Response contract

Lead with:

1. **Conclusion**
2. **Evidence boundary**
3. **Decision or artifact produced**
4. **Risks / unknowns**
5. **Next acceptance event**

Keep the response concise; put durable detail in the artifact. When no file is needed, answer directly instead of creating ceremony.

## Quality check

Before finishing, verify:

- The product problem is not merely a feature restatement.
- Claims can be traced to evidence or are labeled as assumptions.
- Scope and non-goals are both visible.
- Acceptance criteria are observable and falsifiable.
- State, authority, and external-write boundaries are explicit.
- The next event has an owner or a clear user decision.
