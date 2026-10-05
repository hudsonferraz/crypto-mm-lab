# Feature Specification: Product Lab Surface (A+B+C)

**Feature Branch**: `001-product-lab-surface`

**Created**: 2026-10-05

**Status**: Implemented

**Input**: User description: "Ship all packages for Crypto MM Lab product story: A demo-honest dashboard + defaults, B hosted lab UX (tabs, narrative, deploy story), C differentiators (latency/queue fill model, streaming or replay books, scenario sweep UI, operator auth for hosted demos)."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Dashboard makes the lab honest at a glance (Priority: P1) — Package A

An interviewer or researcher opens `/dashboard` and immediately understands this is **paper simulation**, which exchange/symbol/strategy/fill mode are active, whether DEX is on, and if the loop has an error — without reading the README first.

**Why this priority**: First 10 seconds decide whether this feels like a product or a raw metrics page.

**Independent Test**: Load dashboard against a running app (DEX on or off); verify banner, config chips, error surface, quotes, assumptions link, kill confirm — without backtest/WS features.

**Acceptance Scenarios**:

1. **Given** the dashboard loads, **When** viewed above the fold, **Then** a persistent **PAPER SIMULATION** (or equivalent) banner is visible.
2. **Given** the live loop is configured, **When** status is shown, **Then** chips/labels show at least: exchange, MM symbol, strategy, fill mode, DEX on/off.
3. **Given** `last_error` is set, **When** the dashboard refreshes, **Then** the error is visible without opening logs.
4. **Given** open quotes exist, **When** viewing the live panel, **Then** current bid/ask quotes are listed (not only mid/PnL).
5. **Given** a recent tick id exists, **When** the user follows the audit affordance, **Then** they can open tick audit detail (`/audit/ticks/{id}` or equivalent UI).
6. **Given** the user clicks kill switch, **When** confirming, **Then** a confirm step is required before the request fires.
7. **Given** the dashboard chrome, **When** looking for product context, **Then** a link to simulation assumptions / design decisions is present.

---

### User Story 2 - Defaults and Compose match the marketed story (Priority: P1) — Package A

A new clone using `.env.example` and Docker Compose sees a coherent demo: spreads can beat fees, DEX panel is either live or clearly disabled (not silently empty), and portfolio metrics match the repo.

**Why this priority**: Silent wrong economics / missing DEX kill the demo narrative.

**Independent Test**: Diff defaults vs README claims; run Compose; check portfolio EN/PT + `lib/data.ts` test counts.

**Acceptance Scenarios**:

1. **Given** default settings, **When** comparing quote spread to maker fee, **Then** default round-trip fee drag does not equal-or-exceed earned spread without documentation — defaults favor a healthy paper demo (spread meaningfully above maker fee).
2. **Given** Docker Compose defaults, **When** DEX is intended as a headline feature, **Then** DEX is enabled **or** the UI shows an explicit “DEX disabled” state with why — never a blank panel pretending live data.
3. **Given** CEX MM symbol and CEX–DEX compare symbol differ (e.g. BTC vs ETH), **When** viewing Opportunities / DEX panel, **Then** the UI labels both markets and notes quote-asset basis (e.g. USDT vs USDC) so edges are not mistaken for risk-free arb.
4. **Given** portfolio case study copy, **When** compared to README, **Then** test counts and capability claims match (no stale “116 tests” if suite is 114+).

---

### User Story 3 - Lab tabs: Live, Opportunities, Research (Priority: P1) — Package B

A user can switch dashboard tabs among **Live MM**, **Opportunities**, and **Research** (backtest / strategy compare). Research runs against fixtures or stored snapshots and shows Sharpe / drawdown / fill-rate style metrics without leaving the browser.

**Why this priority**: Turns CLI-only research into a lab product surface.

**Independent Test**: Use Research tab with bundled fixture; use Opportunities with DEX on/off; Live remains usable alone.

**Acceptance Scenarios**:

1. **Given** the dashboard, **When** navigating primary IA, **Then** three tabs exist: Live, Opportunities, Research.
2. **Given** Research tab, **When** running the default fixture comparison, **Then** a table for the three strategies (pure / skew / vol) shows comparable metrics already produced by the comparison service.
3. **Given** Opportunities tab, **When** DEX is disabled, **Then** a clear empty/disabled state appears; when enabled, opportunity rows and basis labels appear.
4. **Given** Live tab, **When** used alone, **Then** prior Live metrics (PnL, equity, blotter) still work.

---

### User Story 4 - Hosted demo + interview narrative (Priority: P2) — Package B

A hiring manager can open a public demo URL (when deployed) that is read-oriented and safe. README / docs explain Funttastic → this lab mapping and deploy steps. Portfolio gains a demo link field when URL exists.

**Why this priority**: Clickable demo closes the portfolio loop; narrative ties crypto background.

**Independent Test**: Follow deploy doc dry-run; read interview blurb; portfolio messages accept optional demo URL without requiring live host in CI.

**Acceptance Scenarios**:

1. **Given** deploy documentation, **When** followed for a single-service host, **Then** steps cover env vars, paper-only reminder, and which routes are public vs operator-protected.
2. **Given** `docs/interview-story.md` (or README section), **When** read, **Then** it maps inventory skew, kill switch, stale guards, auditability to trading-systems work and states what is *not* claimed (live keys, queue HFT, guaranteed edge).
3. **Given** portfolio crypto MM case study, **When** a demo URL is configured, **Then** links include demo; when not, source-only remains valid.
4. **Given** a public demo process, **When** kill switch / mutating controls exist, **Then** they require operator authentication (see US6) or are disabled in public mode.

---

### User Story 5 - More realistic paper fills (latency + probability) (Priority: P2) — Package C

A researcher can select a fill mode that models delayed / probabilistic fills (toy queue/latency), compare it to existing modes, and see it labeled on the dashboard.

**Why this priority**: Addresses the #1 skeptical MM interviewer objection without pretending to be an exchange simulator.

**Independent Test**: Unit tests for the new fill mode; dashboard chip shows mode name; backtest/compare can select it.

**Acceptance Scenarios**:

1. **Given** fill mode `latency_prob_fill` (name may vary), **When** a quote would fill under `full_cross_fill`, **Then** fills may be delayed and/or skipped per configurable latency and probability parameters.
2. **Given** the new mode, **When** documented, **Then** README/design-decisions state it is a toy model, not exchange matching-engine fidelity.
3. **Given** strategy comparison / Research tab, **When** comparing modes or strategies, **Then** the new mode is selectable or included in an explicit scenario (see US7).

---

### User Story 6 - Operator auth for dangerous actions (Priority: P1) — Package C / B safety

Mutating operator actions (at least kill switch; optionally loop start/stop if exposed) require a shared operator token when `OPERATOR_API_TOKEN` is set. Read-only status/metrics/market endpoints remain open for demo viewing.

**Why this priority**: Required for any honest hosted demo.

**Independent Test**: With token unset (local default), behavior stays convenient; with token set, unauthenticated kill returns 401/403; authenticated succeeds.

**Acceptance Scenarios**:

1. **Given** `OPERATOR_API_TOKEN` unset, **When** calling kill switch locally, **Then** existing local-dev convenience remains (documented).
2. **Given** token set, **When** kill is called without credential, **Then** request is rejected.
3. **Given** token set, **When** kill is called with correct credential header, **Then** kill succeeds.
4. **Given** dashboard kill UI, **When** token mode is on, **Then** UI can supply the token (prompt/local setting) or clearly states operator-only.

---

### User Story 7 - Scenario sweep UI (Priority: P2) — Package C

From Research, a user runs a small parameter sweep (e.g. strategies × spread or fill mode) on a fixture and downloads or views a results table.

**Why this priority**: Turns “lab” into an interactive research product.

**Independent Test**: Sweep against fixture returns deterministic rows; invalid ranges error clearly.

**Acceptance Scenarios**:

1. **Given** Research → Scenario sweep, **When** user runs default sweep, **Then** results include multiple rows with strategy and key params + metrics.
2. **Given** a completed sweep, **When** export is offered, **Then** user can copy/download CSV or JSON of results.
3. **Given** sweep bounds that are empty/invalid, **When** submitted, **Then** a validation error is shown (no silent empty success).

---

### User Story 8 - Fresher market data path (Priority: P3) — Package C

Users can run market data via **websocket** (when enabled) or fall back to polling; if WS is unavailable, recorded book replay remains supported for demos.

**Why this priority**: Credibility upgrade; polling stays default for reliability.

**Independent Test**: Polling default unchanged; WS mode tested with mock; replay path still feeds backtest/live-lab demo.

**Acceptance Scenarios**:

1. **Given** default config, **When** loop runs, **Then** polling behavior remains the supported default.
2. **Given** `MARKET_DATA_MODE=websocket` (or equivalent), **When** exchange stream is available, **Then** books update from the stream adapter with clear status if disconnected (fallback to poll or pause with error — documented).
3. **Given** offline demo needs, **When** using fixture/replay, **Then** Research and/or Live-lab replay still function without public network.

### Edge Cases

- DEX RPC rate-limited: Opportunities show explicit error, not zeros-as-truth.
- Kill confirm cancel: no request sent.
- Operator token misconfigured empty string: treat as unset or reject boot — pick one and document.
- Latency fill with probability 0: never fills; probability 1 + latency 0: behaves like aggressive fill when crossed.
- Hosted demo without Postgres: SQLite path remains valid.
- Portfolio deploy before demo URL exists: no broken “Live Demo” badge.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Dashboard MUST show a persistent paper-simulation banner and live config chips (exchange, MM symbol, strategy, fill mode, DEX enabled).
- **FR-002**: Dashboard MUST surface `last_error`, open quotes, assumptions/docs link, tick-audit affordance, and kill-switch confirmation.
- **FR-003**: Default spread/fee settings MUST favor a coherent paper demo (spread not dominated by maker fee) and be documented.
- **FR-004**: Compose/UI MUST not silently omit DEX — enable by default in Compose **or** show explicit disabled state.
- **FR-005**: Opportunities/DEX UI MUST label MM vs compare symbols and USDT/USDC (or quote-asset) basis risk.
- **FR-006**: Portfolio EN/PT + metrics MUST match current test/capability claims; demo link optional until hosted.
- **FR-007**: Dashboard MUST provide Live / Opportunities / Research tabs; Research MUST run strategy comparison on a fixture.
- **FR-008**: Docs MUST include deploy guide for a hosted paper demo and an interview-story mapping section.
- **FR-009**: System MUST support a latency/probability fill mode with tests and honest documentation.
- **FR-010**: When operator token is configured, mutating kill (and any new mutate routes) MUST require it; reads stay public.
- **FR-011**: Research MUST support a small scenario sweep with tabular results and export.
- **FR-012**: System MUST keep polling as default market-data mode and add optional websocket mode and/or strengthened replay path with status honesty.
- **FR-013**: Existing paper loop, risk limits, tick audit, backtest CLI, Prometheus metrics, and test/CI gates MUST keep working.
- **FR-014**: Automated tests MUST cover new fill mode math, operator auth gate, scenario sweep validation, and critical dashboard API fields used by the new UI.

### Key Entities

- **Lab runtime config**: Exchange, symbols, strategy, fill mode, DEX flag, market-data mode, fee/spread, operator-token presence.
- **Dashboard view-model**: Banner, chips, errors, quotes, tabs, opportunity rows, research results.
- **Fill simulation policy**: Mode name + parameters (latency, probability) producing fill events.
- **Scenario sweep job**: Parameter grid + fixture id → metric rows.
- **Operator credential**: Shared secret gating mutating HTTP actions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A new visitor identifies “paper simulation” and active strategy/fill mode within 5 seconds on `/dashboard`.
- **SC-002**: Default demo settings do not make round-trip maker fees exceed quoted spread without an on-screen or README explanation.
- **SC-003**: With DEX disabled, 100% of Opportunity views show an explicit disabled/error state (no fake zero edge).
- **SC-004**: Research fixture compare completes in under 30 seconds locally and shows all three strategies.
- **SC-005**: With operator token set, unauthenticated kill attempts fail in automated tests (100%).
- **SC-006**: Latency/prob fill mode unit tests demonstrate both “fill” and “no-fill” outcomes under fixed seeds.
- **SC-007**: Scenario sweep returns ≥3 result rows on the default fixture grid.
- **SC-008**: Portfolio crypto MM copy has zero stale test-count / “no queue-latency model” contradictions after ship (constraints updated for new mode).

## Assumptions

- Paper-only remains absolute — no live order placement, no wallet keys.
- **Hosted demo** may be documented + config-ready in this feature; actual cloud account provisioning can be done by the human after merge (URL plugged into portfolio when live).
- **Websocket** targets Binance public book stream (or CCXT pro if already viable); if exchange blocks environment, fallback/error honesty satisfies FR-012.
- **Uniswap V3** is out of scope for this feature; CEX/DEX story stays V2 observational (+ clearer labeling). Multi-pool V2 is optional stretch only if time remains after sweep/WS/auth.
- Default MM symbol may stay `BTC/USDT` with labeled ETH compare — full symbol unification is optional if UI labeling is clear.
- Local default remains tokenless for DX; production/demo compose sets token.
- Sibling `portfolio-website` updates are part of story sync acceptance.
- Speckit artifacts live under `specs/001-product-lab-surface/` in this repo (new Speckit adoption).
