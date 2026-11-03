# Tasks: Product Lab Surface

- [x] T001 Settings defaults + latency/prob + operator token + market_data_mode; .env.example; compose DEX on
- [x] T002 Implement `latency_prob_fill` in fill_model + paper_broker state; tests
- [x] T003 Operator auth helper; protect POST /kill-switch; tests
- [x] T004 Enrich GET /status|/config|/quotes|/amm|/opportunities with labels + open quotes
- [x] T005 Research APIs: compare + sweep; tests
- [x] T006 Optional websocket adapter + loop injection; poll default; tests/mocks
- [x] T007 Rewrite dashboard.html (banner, chips, tabs, confirm, research UI)
- [x] T008 Docs: design-decisions, interview-story, deploy guide, README
- [x] T009 Portfolio EN/PT + data.ts sync
- [x] T010 Full pytest; fix regressions

## Notes

- **122 tests** passed on Python 3.11.
- Hosted demo URL still human-provisioned (`docs/deploy-demo.md`); portfolio demo link left commented.
- Websocket mode falls back to REST when `watch_order_book` unavailable.

## Fix (2026-11-03): latency_prob_fill never filled in the loop

- [X] T011 In app/execution/paper_broker.py, keep a crossed quote resting (do not replace it on its side) in `latency_prob_fill` mode until it fills or the cross ends; add tests in tests/test_latency_prob_fill.py; update the README and spec 005 notes
