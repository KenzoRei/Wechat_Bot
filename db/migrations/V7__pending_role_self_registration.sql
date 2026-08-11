\encoding UTF8
-- ============================================================
-- V7: pending role for self-registration
-- Logistics WeChat Bot Platform
-- Date: 2026-08-10
--
-- Phase 4 (docs/ai-collaboration/phase4-self-registration.md, jointly
-- signed by both agents, user-approved): a new member self-registers via an
-- exact deterministic pre-access command into a zero-permission `pending`
-- role. No group_service_role grant rows are inserted for it -- the
-- existing deny-by-default role-grant mechanism (access_control.check_access
-- resolving allowed_services from group_service_role per-role) already
-- produces an empty allowed_services list for a role with zero grant rows,
-- with no special-casing required. An admin later promotes a pending member
-- to a real operational role via the existing role_change service.
-- ============================================================

INSERT INTO role (name, description)
VALUES ('pending', 'Self-registered member awaiting admin role assignment -- zero service grants');
