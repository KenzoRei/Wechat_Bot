# ADR-006: Handler pattern — thin inheritance with Workflow Engine owning the lifecycle

**Date:** 2026-04-26
**Status:** Decided

## Decision
Use a thin class hierarchy for handlers. Each handler executes exactly one workflow step. The Workflow Engine owns the request lifecycle (validate, execute steps, log). Handlers do not contain lifecycle logic.

## Original design (rejected)
The original design embedded validate(), record_history(), and reply() inside every handler via a BaseHandler.run() method. This caused:
- Duplicate validation on every step in a multi-step workflow
- Multiple WeChat replies sent per request (one per handler)
- Logging triggered multiple times per request
- Workflow-level concerns mixed into step-level code

## Final handler structure

```
BaseHandler  (abstract)
│   handle(context, config) → dict    ← the only method; the contract
│
└── YDDLabelBaseHandler  (abstract)
│   │   handle()          ← calls _call_api() then _parse_result()
│   │   _parse_result()   ← shared: extract tracking number + label URL
│   │   _call_api()       ← abstract; children implement carrier-specific call
│   │
│   ├── FedExLabelHandler
│   │       _call_api()   ← FedEx-specific YiDiDa parameters
│   │
│   └── UPSLabelHandler
│           _call_api()   ← UPS-specific YiDiDa parameters
│
├── OMSRecordHandler
│       handle()          ← calls OMS API, returns record ID
│
└── ReplyWeChatHandler
        handle()          ← sends message via WeChat API Client


Handler Registry:
{
    "create_fedex_label"  →  FedExLabelHandler,
    "create_ups_label"    →  UPSLabelHandler,
    "oms_record"          →  OMSRecordHandler,
    "reply_wechat"        →  ReplyWeChatHandler
}
```

## Workflow Engine lifecycle (not the handlers)
```python
validate(context)                        # once, before any steps
for each WorkflowStep in order:
    handler = registry[step.step_type]
    result  = handler.handle(context, step.config)
    context.update(result)               # results flow to next step
request_logger.write(context)            # once, after all steps
```

## Reason for YDDLabelBaseHandler
FedEx and UPS label creation go through the same YiDiDa API platform and share:
- The same result format (tracking number + label URL)
- The same error handling patterns
- The same response parsing logic

Only the carrier-specific request parameters differ — handled via _call_api() override in each child.

## Per-customer variation
Different customers using the same carrier handler may have different API parameters (account numbers, billing type, packaging defaults). These are stored in WorkflowStep.config (JSONB) per group. The handler reads from config, so one handler class serves all customers for that carrier.

## Extensibility
- New carrier: add one handler class + one registry entry
- New customer parameters: add WorkflowStep row with different config values — no code changes
- New workflow step type: add one handler class + one registry entry

## Consequences
- Handlers must be stateless — all state lives in context passed between steps
- Each handler is independently testable with mock context and config
- WorkflowStep.config must be well-documented per handler so admins know what fields to set
