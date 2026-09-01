-- V20: point the seeded Primary Kefu Admin row at the current live Kefu
-- account's open_kfid, again.
--
-- V19 already did this once for the account that replaced the original
-- banned one. The account changed again during the production-environment
-- migration (new server, new WECHAT_KEFU_OPEN_KFID), so the same fix is
-- needed a second time. A .sql migration file has no way to read the
-- runtime WECHAT_KEFU_OPEN_KFID env var, so this stays a literal value
-- that must be kept in sync by hand whenever the live account changes --
-- see main.py's open_kfid gate for the other half of this sync
-- requirement.
--
-- Idempotent: a no-op if already applied (row already has the new
-- open_kfid) or if the old row no longer exists for any reason.

UPDATE kefu_staff
SET open_kfid = 'wkY-jPKwAAaeXCLS1qU2jkT5qQ7rwpIg',
    updated_at = now()
WHERE open_kfid = 'wkY-jPKwAA8oJupr_zeuJfGF4uJpQG3w'
  AND external_userid = 'wmY-jPKwAAxWutsejG7W9dRdugWOQ3oQ';
