# Logistics WeChat Bot Platform — Project Kickoff Doc

## Company Context
- Medium-size logistics company based in NYC
- Own carrier accounts: UPS, FedEx (managed via YiDiDa platform)
- Currently collect shipping label requests via WeChat group manually
- Goal: automate request collection and label creation end-to-end

---

## Problem Statement
Staff manually read WeChat group messages and create labels on YiDiDa. This is slow, error-prone, and doesn't scale. We want a WeChat bot to collect structured requests and trigger the correct workflow automatically.

---

## Key Constraints & Inputs
- Volume: <50 label requests/day
- Input format: structured messages (address, weight, service type, etc.) but typed by humans so normalization is needed
- WeChat environment: **企业微信 (WeChat Work)** — all staff and customers are already in it
- Customers join via personal WeChat (外部联系人 feature) — no app switch needed for them
- YiDiDa has an **official API** for label creation
- OMS also has **official API endpoints**

---

## System Architecture

```
企业微信 Group Message
        │
        ▼
┌─────────────────────┐
│   Webhook Receiver  │  validates WeChat signature
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   Access Control    │  checks User + Group + ServiceType
│   Middleware        │  against DB — rejects if not in serving list
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   Request Parser    │  Claude API normalizes message
│   (LLM layer)       │  → structured JSON per input_schema
└────────┬────────────┘
         │
         ▼
┌─────────────────────┐
│   Workflow Engine   │  looks up workflow, runs steps in order
│                     │  each step is a pluggable handler
└────────┬────────────┘
         │
    ┌────┴──────────────────────────┐
    ▼                               ▼
YiDiDa API                 OMS API / other APIs
(label creation)           (warehouse-in, history record...)
    │                               │
    └───────────────┬───────────────┘
                    ▼
           Reply to WeChat Group
           (tracking #, label PDF, confirmation...)
```

---

## Tech Stack

| Layer | Choice |
|---|---|
| Backend | Python / FastAPI |
| Database | PostgreSQL |
| ORM | SQLAlchemy |
| LLM Parsing | Claude API (claude-sonnet-4-20250514) |
| Hosting | AWS EC2 or Railway |
| Queue (optional later) | Redis + RQ |

---

## Database Design

### Company
Stores per-company API credentials and info.
```
Company
├── company_id        PK
├── company_name
├── oms_api_key
├── yidida_api_key
├── fedex_account
└── ups_account
```

### User
Individual WeChat users (customers or staff).
```
User
├── user_id           PK
├── wechat_openid     unique
├── wechat_username
├── display_name
└── company_id        FK → Company
```

### GroupConfig
Each 企业微信 customer group and what services it allows.
```
GroupConfig
├── group_id          PK
├── wechat_group_id   unique
├── allowed_service_types[]
└── description
```

### UserGroupService ← the permission junction table
Three-way junction capturing user + group + service permission.
This solves the case where the same user has different permissions in different groups.
```
UserGroupService
├── user_id           FK → User
├── group_id          FK → GroupConfig
└── service_type_id   FK → ServiceType
```

Access check query:
```sql
SELECT 1 FROM UserGroupService
WHERE user_id = ? AND group_id = ? AND service_type_id = ?
```

### ServiceType
Each service the platform supports.
```
ServiceType
├── service_type_id   PK
├── name              e.g. "fedex_label", "warehouse_in"
├── workflow_id       FK → Workflow
└── input_schema      JSON Schema — defines required fields for this service
```

### Workflow + WorkflowStep
Defines the ordered sequence of steps for each service type.
```
Workflow
├── workflow_id       PK
├── name
└── steps[]

WorkflowStep
├── step_id           PK
├── workflow_id       FK → Workflow
├── order             integer
├── step_type         e.g. "create_label", "oms_record", "reply_wechat"
└── config            JSON — step-specific parameters
```

---

## Handler Architecture

Uses **Template Method + Inheritance** pattern. Shared logic lives in base classes. Each handler only overrides what's unique to it.

### Class Hierarchy
```
BaseHandler  (abstract)
├── run()                 ← orchestrates all steps, never overridden
├── validate()            ← checks required fields from input_schema
├── execute()             ← MUST be overridden
└── record_history()      ← always logs to OMS

        ├── LabelBaseHandler  (abstract)
        │   ├── execute()         ← shared label creation logic
        │   ├── send_label()      ← sends PDF back to WeChat
        │   ├── FedExLabelHandler
        │   │   └── execute()     ← FedEx-specific API call via YiDiDa
        │   └── UPSLabelHandler
        │       └── execute()     ← UPS-specific API call via YiDiDa
        │
        └── OMSBaseHandler  (abstract)
            ├── execute()         ← shared OMS workflow logic
            └── WarehouseInHandler
                └── execute()     ← warehouse-in specific logic
```

### BaseHandler.run() lifecycle — guaranteed for every handler
```python
class BaseHandler:
    def run(self, context):
        self.validate(context)               # 1. validate input
        result = self.execute(context)       # 2. do the actual work
        self.record_history(context, result) # 3. always log
        self.reply(context, result)          # 4. always reply to WeChat
```

---

## Extensibility Rules

| Change needed | What you do |
|---|---|
| New customer | Insert rows into Company, User, UserGroupService |
| New group rule | Insert/update GroupConfig |
| New service type | Insert ServiceType + Workflow + WorkflowStep rows |
| New workflow action | Write one handler file, register it |
| New API integration | Write adapter, use in handler |

---

## Current Progress
- [x] Requirements gathered
- [x] System architecture designed
- [x] Database schema designed
- [x] Handler pattern decided
- [ ] Finalize database schema with full field types and constraints
- [ ] Design API interfaces between modules
- [ ] Design 企业微信 webhook receiver module
- [ ] Design Claude API parsing layer
- [ ] Begin implementation

---

## Immediate Next Step
**Phase 3: Finalize Data Modeling** — write out the full DB schema with actual PostgreSQL field types, constraints, indexes, and relationships before any code is written.
