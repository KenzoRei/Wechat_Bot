# ADR-003: Use SQLAlchemy as the ORM

**Date:** 2026-04-26
**Status:** Decided

## Decision
Use SQLAlchemy as the ORM (Object Relational Mapper).

## Alternatives considered
- **Django ORM** — tightly coupled to Django; not available outside a Django project; eliminated when we chose FastAPI
- **Raw SQL** — maximum control but requires manual query writing, result parsing, connection management, and parameter binding for every database interaction; not maintainable across 10+ tables

## Reason
- FastAPI + SQLAlchemy is the industry standard pairing — extensive documentation, tutorials, and community support
- Allows writing database queries in Python rather than raw SQL strings, reducing errors and improving readability
- Supports JSONB columns natively for PostgreSQL
- Works alongside Alembic for database migration management
- Developer's existing PostgreSQL knowledge transfers directly — SQLAlchemy is a Python interface to the same database concepts already understood

## Consequences
- Adds a layer of abstraction over SQL — developers must understand both SQLAlchemy syntax and the underlying SQL it generates
- Alembic required for schema migrations (added as a project dependency)
