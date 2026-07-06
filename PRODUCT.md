# Product

## Register

product

## Users

One self-directed investor/engineer (the repo owner) doing personal equity research and portfolio bookkeeping. Context: evenings/weekends at a desk, unhurried, reading research reports and portfolio signals before making their own trade decisions. The job: understand a stock or the portfolio well enough to decide — the app computes, the human decides.

## Product Purpose

AI-aided equity research and portfolio decision support. Deterministic Python computes every indicator, confidence grade, and portfolio number; an LLM only narrates already-computed values (with a skeptical critic pass and fabrication check). Portfolio module: holdings/cash, broker sync (SnapTrade/Fidelity), optimization, backtests, correlation/drift/regime signals, rebalance plans, transaction ledger. Explicitly not a trading or advice product. Success = the owner trusts the numbers and reads the reports instead of juggling spreadsheets.

## Brand Personality

Warm, personal, grounded. A private tool that feels like a well-kept notebook, not an institution: friendlier microcopy and more personality than typical finance software, while the underlying data stays precise and skeptical. Emotional goal: calm confidence — never urgency, never hype. Visual identity: the "evening-study ledger" — soft mint ink on deep sea charcoal, hairline rules, Source Serif 4 money figures over Source Sans 3 UI, one sage-green accent (see `app/web/static/app.css`, the entire system).

## Anti-references

- **Robinhood-style gamification**: no confetti, streaks, dopamine-green P/L emphasis, or urgency cues. Gains/losses are information, not rewards.
- **Generic SaaS dashboard**: no gray card grids, hero-metric templates, or shadcn-default look. The coastal palette and serif display headings exist precisely to avoid this.
- **Crypto/fintech hype**: no dark-neon gradients, glow charts, or "number go up" energy.

## Design Principles

1. **The human decides** — surfaces present evidence and computed signals; they never nudge toward action. Copy says "context", not "buy/sell".
2. **Numbers are sacred** — every displayed figure is computed in code (see CLAUDE.md hard rules). Design must make provenance and confidence grades legible, not decorate them away.
3. **Warm shell, precise core** — friendly voice and calm chrome/microcopy; unflinching precision in tables, scorecards, and charts.
4. **Meaning-bearing color only** — bullish/bearish/neutral badges and the diverging correlation palette carry semantics; decorative color must never collide with them. Data viz stays colorblind-safe (Okabe-Ito).
5. **One reader, no onboarding theater** — it's a personal tool; optimize for the returning expert user, not first-run conversion.

## Accessibility & Inclusion

Best-effort, not conformance-gated: avoid obvious failures (readable contrast, colorblind-safe charts — already Okabe-Ito, `prefers-reduced-motion` respected where motion exists), but don't block work on strict WCAG auditing. Single committed dark theme (evening-study ledger); no theme toggle.
