# Quickstart: Volatility Term

```bash
py -3.12 scripts/calibrate_fill_model.py
```

It prints, per held-out session: spread, median volatility, unseen fills, and for
the base and volatility forms the expected count, the drawn count and the look
log-likelihood. Then the gate line, and it writes the form the gate chose.

In the dashboard's Testnet tab, the calibration card shows the form in use and the
two-form table. `GET /testnet/calibration` returns the same.

## Evidence

| Check | Result |
|---|---|
| Recordings | 4 new sessions (426494 6 bps, eca4c8 2 bps, f0af28 6 bps, ef78ea 2 bps), 30 min each, 2026-11-04 morning; all 8 pass the secret scan |
| Replay of every stored model | 0 mismatches on all 8 recordings, `calibrated_fill` included, with each recording's used calibration |
| Volatility vs unseen fills per session | evening 0.25/9, 0.40/11, 1.07/26, 0.84/20; morning 0.45/20, 0.53/55, 0.70/40, 0.58/56 (bps/√s / unseen fills) |
| Gate | volatility form better in 4 of 8 held-out sessions (needs 6), k = 0.26 > 0: not passed, base form ships |
| Repeatable, runtime | rebuild equals the shipped file (test); about 3 s offline |
| Fill step cost | +11 µs p95 per tick over `full_cross_fill` (9.8 to 21.1 µs), tick p95 about 6.9 s |
