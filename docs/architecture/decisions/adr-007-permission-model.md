# ADR-007: Group-level permission model

**Date:** 2026-04-26
**Status:** Decided

## Decision
Use a group-level permission model. Permissions are assigned to groups, not to individual users. All members of a group inherit the group's permissions automatically.

## Original design (rejected)
A three-way junction table (UserGroupService) granted permission at the intersection of user + group + service type. This required inserting one row per user per group per service type combination — O(users × groups × services) rows. Adding one new user to a group with 3 services required 3 inserts. Adding one new service to a group with 10 users required 10 inserts.

## Final permission model

```
GroupConfig
├── group_id (PK)
├── wechat_group_id (unique)
└── description

GroupMember
├── wechat_openid         ← user identifier (WeChat's permanent internal ID)
├── group_id (FK → GroupConfig)
├── role                  admin | customer
├── display_name          stored here since no separate User table
└── is_active             allows suspending one user without removing them

GroupService
├── group_id (FK → GroupConfig)
└── service_type_id (FK → ServiceType)
```

## Permission check flow
```
1. Is wechat_openid in GroupMember for this group?
   → NO:  reply with no-permission message
   → YES: continue

2. Load group's allowed services from GroupService
3. Pass allowed services to AI Provider Chain with user message
   → AI classifies service type and validates against allowed list in one step
```

## Why no separate User table
- wechat_openid is WeChat's permanent internal user ID — stable enough to use as the primary user identifier
- display_name stored in GroupMember (a user may have different display names in different groups)
- request_log references wechat_openid directly — sufficient for audit and history purposes
- Eliminates one table and one join from every permission check

## Why wechat_openid not wechat username/alias
- WeChat username/alias can be changed by the user at any time — unreliable as an identifier
- wechat_openid is assigned by WeChat internally and does not change

## Role design
- v1 has two roles: admin and customer
- Bot only responds to these two roles
- Additional roles deferred to v2
- is_active flag on GroupMember allows suspending individual users without removing their membership record

## Extensibility
- New user: one INSERT into GroupMember
- New service for a group: one INSERT into GroupService
- User-level permissions: can be layered on top in v2 without restructuring this model

## Consequences
- All users in a group have identical service permissions — by design for v1
- Hierarchical permissions within a group require splitting into separate groups
- No per-user permission granularity in v1
