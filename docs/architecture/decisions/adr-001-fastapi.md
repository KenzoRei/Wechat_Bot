# ADR-001: Use FastAPI as the web framework

**Date:** 2026-04-26
**Status:** Decided

## Decision
Use FastAPI as the backend web framework.

## Alternatives considered
- **Flask** — minimal framework, lacks native async support, requires too many third-party additions for a production API service
- **Django** — full-stack framework designed for web applications with user-facing pages; too heavyweight for a pure webhook API service; includes features (admin panel, templating, session auth) we do not need

## Reason
- Purpose-built for API services — exactly our use case
- Native async support: our system spends most of its time waiting on external APIs (Claude, YiDiDa, WeChat, OMS); async allows handling multiple requests concurrently without blocking
- Auto-generates OpenAPI documentation from code — useful for Phase 4 API contract design
- FastAPI + SQLAlchemy is the industry standard pairing with extensive documentation

## Consequences
- Team needs familiarity with Python async/await patterns
- No built-in admin UI — admin operations handled via API endpoints only
