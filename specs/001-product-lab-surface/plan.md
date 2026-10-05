# Implementation Plan: Product Lab Surface

**Branch**: `001-product-lab-surface` | **Date**: 2026-10-05 | **Spec**: [spec.md](./spec.md)

## Summary

Upgrade Crypto MM Lab from ops metrics page to honest paper-trading product: dashboard IA (banner/chips/tabs), coherent defaults + DEX honesty, Research APIs, operator-token kill gate, latency/prob fill mode, scenario sweep, optional websocket market-data mode, docs + portfolio sync.

## Technical Context

**Language/Version**: Python 3.11+  
**Primary Dependencies**: FastAPI, CCXT, web3.py, SQLAlchemy, Prometheus  
**Storage**: SQLite/Postgres (existing)  
**Testing**: pytest + coverage gate  
**Target Platform**: Local + Docker Compose; hosted demo doc-ready  
**Project Type**: Web service + static dashboard  
**Constraints**: Paper-only; no V3; poll default  

## Constitution Check

Hudson Speckit process applies. No local constitution — follow approved spec + existing design-decisions honesty.

## Source touchpoints

```text
app/config/settings.py
app/execution/fill_model.py (+ paper_broker.py)
app/api/routes.py (+ auth helper)
app/static/dashboard.html
app/adapters/cex/ (websocket optional)
app/services/strategy_comparison.py (API wrap)
docker-compose.yml, .env.example
docs/*, README.md
portfolio-website messages + data.ts
tests/*
```

See research.md / tasks.md.
