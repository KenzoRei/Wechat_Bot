# Product Requirements Document
# Logistics WeChat Bot Platform — v1

**Version:** 1.0
**Date:** 2026-04-25
**Status:** Approved

---

## 1. Problem Statement

The company currently processes shipping label requests manually: customers send unstructured messages in WeChat groups, and staff read them, reformat the data, and manually create labels on the YiDiDa platform. This process is slow, error-prone, and does not scale.

**Goal:** Deploy a WeChat Work bot that collects structured shipping requests from customers, normalizes them via AI, and triggers the appropriate backend workflow (label creation, rate quoting, etc.) automatically — with no manual staff intervention in the loop.

---

## 2. Users & Roles

| Role | Description | WeChat Type |
|---|---|---|
| **Customer** | External customer requesting logistics services (label creation, rate quotes, etc.) | Personal WeChat (外部联系人) |
| **Admin** | Company staff managing the platform; receives failure notifications | 企业微信 (WeChat Work) |

> v2 will introduce additional roles (e.g. operations staff with different permissions). For v1, all internal users are Admin.

---

## 3. Functional Requirements

### 3.1 Bot Presence & Activation
- The bot must be present in 企业微信 group chats
- A customer activates the bot by @mentioning it in the group
- The bot must not respond to messages that do not @mention it

### 3.2 Request Classification
When a user @mentions the bot, the bot must classify the message into one of three categories and respond accordingly:

| Category | Bot Behavior |
|---|---|
| **Authorized service request** | Begin the info-collection flow for that service |
| **Unauthorized service request** | Reply in Chinese informing the user they do not have access to that service in this group |
| **Unrelated / conversational message** | Reply in Chinese politely declining and ending the conversation |

### 3.3 Info Collection Flow
Once an authorized service request is identified:

1. **Field prompting** — Bot asks for any required fields that were not provided in the initial message. It may @mention specific users (including Admin) in the group to request missing information.
2. **AI normalization** — Before presenting data to the user, the bot normalizes the input using AI. Examples:
   - Split a single-line address into street, city, state, zip
   - Standardize phone number formats
   - Correct obvious typos in city/state names
3. **Confirmation** — Bot presents all collected and normalized fields in a structured summary and asks the customer to confirm.
4. **Transfer** — After explicit customer confirmation, the request is submitted to the backend. The customer is informed that processing has begun.

### 3.4 Request Tracking
- Every request is assigned a human-readable serial number: `REQ-YYYYMMDD-NNNN` (e.g. `REQ-20260425-0001`)
- This number is shown to the customer at confirmation and in all subsequent updates

### 3.5 Result Notification
After backend processing, the bot must reply in the same WeChat group:

| Outcome | Bot Action |
|---|---|
| **Success** | Return the result to the customer (e.g. tracking number, label PDF, shipping rate) |
| **Failure or timeout** | Notify the customer that the request failed; notify Admin in the same group with the serial number and error summary |

### 3.6 Language
- All bot replies are in **Chinese (Simplified)** for v1
- The AI parser accepts input in any language (customers may write in English or Chinese)

---

## 4. Non-Functional Requirements

### 4.1 Performance
| Requirement | Target |
|---|---|
| Webhook acknowledgement to WeChat | < 5 seconds (WeChat retries if not met) |
| Bot first reply to user after @mention | < 3 seconds |
| Full round-trip: user confirmation → label result returned | < 60 seconds |
| Full round-trip: user confirmation → rate quote returned | < 10 seconds |

### 4.2 Reliability
| Requirement | Detail |
|---|---|
| Idempotency | Each WeChat message is processed exactly once, even if WeChat retries delivery |
| No duplicate actions | A label is never created without explicit customer confirmation |
| Graceful AI failure | If the AI parsing service is unavailable, bot notifies user in Chinese and logs the failure; does not silently crash |

### 4.3 Security
| Requirement | Detail |
|---|---|
| Webhook validation | Every incoming webhook is signature-validated against WeChat's token before any processing |
| Credential protection | API credentials (YiDiDa, OMS) are never written to logs or returned in any response |
| Access enforcement | Only users present in the permission table can trigger any service; all others receive an unauthorized reply |

### 4.4 Observability
| Requirement | Detail |
|---|---|
| Request logging | Every request is logged from start to finish with status (`pending → success / failed`) |
| Error detail | Error messages stored in database for Admin debugging |
| Admin alerting | Admin is notified in-group for every failure or timeout |

### 4.5 Availability
- System available during business hours: **9am – 7pm ET, Monday – Saturday**
- Planned downtime communicated to Admin 24 hours in advance

### 4.6 Data Retention
- Request logs retained for minimum **90 days**
- Label PDFs stored or linked for minimum **30 days**

---

## 5. Out of Scope — v1

The following are explicitly deferred to future versions. Do not design or build for these in v1.

| Feature | Reason deferred |
|---|---|
| Web admin dashboard | Manual DB access sufficient for v1 at <50 req/day |
| Multi-language bot replies | All customers operate in Chinese context; English support is v2 |
| Payment / billing per request | Handled externally; no requirement in v1 |
| Retry queue (Redis/RQ) | Low volume makes background threads sufficient; Redis adds operational overhead |
| Mobile app | Customers use WeChat natively; no separate app needed |
| Customer feedback collection per request | v2 feature for service evaluation/statistics |
| Auto-restart failed requests with corrected info | v2 feature; Admin handles manually for now |
| Additional user roles (e.g. operations staff) | Only Admin and Customer in v1 |
| Multiple AI provider failover | Design the LLM layer with an adapter interface to make this easy to add in v2 |

---

## 6. Constraints & Assumptions

- **Platform:** 企业微信 (WeChat Work) only. Customers access via personal WeChat through the 外部联系人 feature.
- **Volume:** < 50 service requests per day in v1.
- **External APIs:** YiDiDa and OMS both have official APIs. No screen-scraping required.
- **AI parsing:** Claude API (claude-sonnet-4-20250514). The LLM layer must be built behind an adapter interface so providers can be swapped or chained in v2.
- **Language:** Bot replies in Chinese only. AI parser accepts any language as input.
- **Single company:** v1 serves one company. Multi-tenancy is a future concern.

---

## 7. Open Questions

| Question | Status |
|---|---|
| What is the timeout threshold before a request is marked as failed and Admin is notified? | To be decided in Phase 4 (API design) |
| What happens if a customer takes too long to confirm (e.g. walks away mid-flow)? | To be decided in Phase 5 (module design) |
| Should customers be able to cancel an in-progress collection flow? | To be decided |
