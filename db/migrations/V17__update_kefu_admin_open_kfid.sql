-- V17: point the seeded Primary Kefu Admin row at the current live Kefu
-- account's open_kfid.
--
-- V13 seeded this row against the original Kefu account's open_kfid. That
-- account was later replaced by a new one (created after a WeCom
-- account-level ban/recovery cycle), so kefu_staff's composite
-- (open_kfid, external_userid) key no longer matched -- the same admin,
-- messaging through the new account, was treated as unregistered.
--
-- Idempotent: a no-op if already applied (row already has the new
-- open_kfid) or if the old row no longer exists for any reason.

UPDATE kefu_staff
SET open_kfid = 'wkY-jPKwAA8oJupr_zeuJfGF4uJpQG3w',
    updated_at = now()
WHERE open_kfid = 'wkY-jPKwAAuTdDqw8h66NPzyciJsw-6A'
  AND external_userid = 'wmY-jPKwAAxWutsejG7W9dRdugWOQ3oQ';
