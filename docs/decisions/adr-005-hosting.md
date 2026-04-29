# ADR-005: Use Railway for production hosting, desktop + ngrok for development

**Date:** 2026-04-26
**Status:** Decided

## Decision
- **Development & testing:** local desktop running FastAPI + ngrok for public webhook URL
- **Production (v1):** Railway platform

## Alternatives considered
- **AWS EC2** — full virtual server with complete control; requires significant ops work: server setup, SSL configuration, deployment pipeline, process management, security patching; appropriate when team has DevOps experience or existing AWS infrastructure
- **Desktop only** — not viable for production; no public URL without tunneling; not always running

## Reason for Railway
- Volume of <50 requests/day does not justify the operational overhead of managing a VPS
- Railway handles deployment, HTTPS, process restarts, and environment variables automatically
- Deployment is as simple as pushing to GitHub — appropriate for a solo developer learning the stack
- Cost (~$5/month) is lower than the cheapest EC2 instance at our traffic level
- Can migrate to EC2 or AWS ECS later if the business scales and needs more control

## Reason for ngrok in development
- WeChat webhook requires a publicly accessible HTTPS URL to deliver events
- ngrok creates a temporary public tunnel to localhost — standard practice for webhook development
- Allows testing with real WeChat messages against local code before any deployment

## Consequences
- Railway has less configuration control than EC2 — acceptable for v1
- ngrok free tier generates a new URL each session — WeChat webhook URL must be updated in WeChat dashboard each development session (ngrok paid tier fixes this with a static URL)
- Migration path to EC2/AWS exists if needed in v2
