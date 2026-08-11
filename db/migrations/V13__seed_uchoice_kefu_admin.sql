-- V13: canonical U-Choice group and initial Kefu administrator
--
-- Idempotent for both an existing deployment and a fresh database. The
-- stable group UUID is also the value expected by KEFU_GROUP_ID.

INSERT INTO group_config (
    group_id,
    wechat_group_id,
    description,
    is_active
)
VALUES (
    'd2a2a444-060e-4988-8167-0d4b468113b0',
    'wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A',
    'U-Choice main group',
    true
)
ON CONFLICT (wechat_group_id) DO UPDATE
SET description = EXCLUDED.description,
    is_active = true,
    updated_at = now();

INSERT INTO kefu_staff (
    open_kfid,
    external_userid,
    group_id,
    role_id,
    display_name,
    is_active
)
SELECT
    'wkY-jPKwAAuTdDqw8h66NPzyciJsw-6A',
    'wmY-jPKwAAxWutsejG7W9dRdugWOQ3oQ',
    group_config.group_id,
    role.role_id,
    'Primary Kefu Admin',
    true
FROM group_config
JOIN role ON role.name = 'admin'
WHERE group_config.wechat_group_id = 'wrY-jPKwAAfNXtgmgIBKovuS7Pm6fT6A'
ON CONFLICT (open_kfid, external_userid) DO UPDATE
SET group_id = EXCLUDED.group_id,
    role_id = EXCLUDED.role_id,
    display_name = EXCLUDED.display_name,
    is_active = true,
    updated_at = now();
