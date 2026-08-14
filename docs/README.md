# Documentation index

**Status:** Current
**Owner:** Engineering
**Last reviewed:** 2026-08-14

This directory separates current operating guidance from historical design and
AI-review records. Start with the section matching your task.

## Start here

| Audience or task | Authoritative document |
|---|---|
| Product scope and supported workflows | [Product overview](product/overview.md) |
| System design and channel boundaries | [Architecture overview](architecture/overview.md) |
| Smart Bot/Kefu switching | [Runtime modes](architecture/runtime-modes.md) |
| Tables and schema authority | [Data model](architecture/data-model.md) |
| Environment variables | [Configuration](reference/configuration.md) |
| HTTP endpoints | [API reference](reference/api.md) |
| Deploying and operating Render | [Render deployment](operations/deployment-render.md) |
| Admin API usage | [Admin API](operations/admin-api.md) |
| Adding workflows/services | [Adding a service](operations/adding-a-service.md) |
| Applying SQL migrations | [Migrations](operations/migrations.md) |
| Running tests safely | [Test strategy](testing/strategy.md) |
| Provisioning the integration-test database | [Local PostgreSQL test database](testing/local-postgresql.md) |
| Why an architectural choice exists | [ADRs](architecture/decisions/) |

## Authority and status

Maintained documents use one of these statuses:

- **Current** — expected to match the named verification commit.
- **Draft** — proposed guidance that is not yet authoritative.
- **Superseded** — retained because a newer document or ADR replaced it.
- **Historical** — evidence or design reasoning, not current instructions.

When documents disagree, use this order:

1. Current ADRs.
2. Current product, architecture, reference, operations, and testing docs.
3. Source code, database constraints, and migrations when a maintained document
   has not yet been refreshed.
4. Archived plans, designs, transcripts, and reviews only for historical
   reasoning.

## Archive policy

`archive/` is intentionally excluded from normal operating guidance. Archived
documents preserve authorship, signatures, and decision history, but may refer
to old paths, commits, infrastructure, or behavior. Never copy secrets into an
archive. Security-remediation prompts must be sanitized or deleted rather than
preserved with credential literals.

Active multi-review work belongs under `reviews/active/` and is ignored while
it may contain working material. At closeout, retain a sanitized final plan and
the minimum supporting reviews under `archive/collaboration/`.
