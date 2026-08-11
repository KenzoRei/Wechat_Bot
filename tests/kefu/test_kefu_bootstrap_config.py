import importlib

import config


def test_callback_crypto_does_not_require_full_kefu_credentials(monkeypatch):
    monkeypatch.setenv("KEFU_ENABLED", "false")
    monkeypatch.setenv("WECHAT_CORP_ID", "bootstrap-corp")
    monkeypatch.setenv("WECHAT_KEFU_TOKEN", "bootstrap-token")
    monkeypatch.setenv(
        "WECHAT_KEFU_ENCODING_AES_KEY",
        "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
    )
    monkeypatch.setenv("WECHAT_KEFU_SECRET", "")
    monkeypatch.setenv("WECHAT_KEFU_OPEN_KFID", "")
    monkeypatch.setenv("KEFU_GROUP_ID", "")

    reloaded = importlib.reload(config)

    assert reloaded.WECHAT_KEFU_TOKEN == "bootstrap-token"
    assert reloaded.WECHAT_KEFU_SECRET == ""
    assert reloaded.WECHAT_KEFU_OPEN_KFID == ""
    assert reloaded.KEFU_GROUP_ID == ""
