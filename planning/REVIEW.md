# Review of `planning/PLAN.md`

## Findings

### 1. High: The frontend delivery model is internally inconsistent
References: `planning/PLAN.md:66`, `planning/PLAN.md:398`, `planning/PLAN.md:444`

The plan says the frontend is a static Next.js export served by FastAPI, but the local development section says `docker-compose.yml` will mount `frontend/` as a live volume and rely on backend `--reload` for frontend changes. That does not work as written: FastAPI reload does not rebuild or hot-serve a static Next.js export. Unless the development flow introduces a separate Next dev server, frontend edits will not appear without rebuilding the export.

Recommended fix: define one explicit dev workflow. Either:

- keep the static-export architecture and say local development rebuilds the frontend export on change, or
- run a separate Next.js dev server in development and reserve static export for production image builds.

### 2. High: Core API contracts are too underspecified for parallel implementation
References: `planning/PLAN.md:264`, `planning/PLAN.md:276`, `planning/PLAN.md:284`, `planning/PLAN.md:292`, `planning/PLAN.md:324`

The endpoint table gives paths and brief descriptions, but not the request/response schemas needed for frontend, backend, and tests to converge on the same contract. This is most risky for:

- `GET /api/portfolio`: exact shape of positions, totals, derived fields, and formatting
- `POST /api/portfolio/trade`: success response, validation errors, and whether the updated portfolio is returned
- `GET /api/watchlist`: whether prices are embedded and how missing prices are represented
- `POST /api/chat`: exact structure for assistant text, executed actions, and partial failures
- SSE payloads: whether events are one-per-ticker, batched, replayed on connect, or heartbeat-capable

Recommended fix: add canonical JSON examples for every endpoint, plus an error envelope shared across the API.

### 3. High: Trade execution rules are missing critical correctness details
References: `planning/PLAN.md:27`, `planning/PLAN.md:170`, `planning/PLAN.md:277`, `planning/PLAN.md:318`

The plan states that trades fill instantly at the current price and that both manual and LLM trades use the same validation path, but it does not define several behaviors that materially affect correctness:

- what happens if no live price is available for the ticker
- whether stale cached prices are acceptable and how staleness is defined
- whether ticker symbols are normalized to uppercase
- how fractional quantities are rounded and validated
- how concurrent requests are serialized so cash/position updates stay atomic in SQLite

Without these rules, two agents can implement different trade semantics and still believe they followed the plan.

Recommended fix: add a short “trade execution contract” section covering price source, staleness policy, normalization, precision, and transaction boundaries.

### 4. Medium: The plan mixes project requirements with agent/tooling-specific instructions
References: `planning/PLAN.md:305`, `planning/PLAN.md:307`

The LLM section depends on a `/cerebras` Claude Code skill and mentions that an API key already exists in the project root `.env`. Those are implementation-environment assumptions, not stable project requirements. They make the plan less portable and create avoidable coupling between the product spec and one agent workflow.

Recommended fix: rewrite this section in repo-neutral terms: required env vars, provider path through LiteLLM/OpenRouter, model identifier, mock-mode behavior, and expected structured output. Keep agent-specific workflow instructions outside the product plan.

## Open Questions

1. Is local development supposed to use one container only, or is a separate Next.js dev server acceptable during development?
2. Should `POST /api/chat` return only the assistant turn, or also the resulting portfolio/watchlist state after auto-executed actions?
3. Is “real market data via Massive API” expected to support arbitrary user-added tickers, and if so what is the fallback behavior for invalid or unsupported symbols?

## Overall

The product direction is clear and buildable, but the document is still one level too high-level in the places where multiple agents need exact contracts. Tightening the dev workflow, API schemas, and trade semantics would remove most of the implementation risk.
