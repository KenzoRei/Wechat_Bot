from fastapi import FastAPI
from fastapi.testclient import TestClient
from api.kefu_callback import create_kefu_callback_router, parse_sync_event
from core.WXBizXmlMsgCrypt import WXBizXmlMsgCrypt
from core.wecom_crypto import compute_signature, encrypt_payload


TOKEN = "callback-token"
ENCODING_KEY = "abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG"
RECEIVE_ID = "corp-id"


def test_parse_sync_event_contract():
    event = parse_sync_event(
        "<xml><ToUserName>corp</ToUserName><CreateTime>123</CreateTime>"
        "<MsgType>event</MsgType>"
        "<Event>kf_msg_or_event</Event><Token>sync-1</Token>"
        "<OpenKfId>kf-1</OpenKfId></xml>"
    )
    assert event.sync_token == "sync-1"
    assert event.open_kfid == "kf-1"
    assert event.create_time == 123


def test_parse_sync_event_rejects_xxe_and_unknown_event():
    for payload in (
        '<!DOCTYPE xml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]><xml><Event>&xxe;</Event></xml>',
        "<xml><Event>other_event</Event><Token>sync</Token></xml>",
    ):
        try:
            parse_sync_event(payload)
        except ValueError:
            pass
        else:
            raise AssertionError("unsafe or unrelated callback was accepted")


def test_encrypted_callback_acknowledges_and_schedules_sync():
    crypt = WXBizXmlMsgCrypt(TOKEN, ENCODING_KEY, RECEIVE_ID)
    events = []
    app = FastAPI()
    app.include_router(create_kefu_callback_router(crypt, events.append))
    client = TestClient(app)
    plain = (
        "<xml><ToUserName>corp-id</ToUserName><CreateTime>123</CreateTime>"
        "<MsgType>event</MsgType><Event>kf_msg_or_event</Event>"
        "<Token>sync-1</Token><OpenKfId>kf-1</OpenKfId></xml>"
    )
    encrypted = encrypt_payload(plain, RECEIVE_ID, crypt.key)
    timestamp, nonce = "100", "200"
    signature = compute_signature(TOKEN, timestamp, nonce, encrypted)
    envelope = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"

    response = client.post(
        "/kefu/callback",
        params={"msg_signature": signature, "timestamp": timestamp, "nonce": nonce},
        content=envelope,
    )

    assert response.status_code == 200
    assert response.text == "success"
    assert [event.sync_token for event in events] == ["sync-1"]


def test_callback_rejects_bad_signature_without_scheduling():
    crypt = WXBizXmlMsgCrypt(TOKEN, ENCODING_KEY, RECEIVE_ID)
    events = []
    app = FastAPI()
    app.include_router(create_kefu_callback_router(crypt, events.append))
    response = TestClient(app).post(
        "/kefu/callback",
        params={"msg_signature": "bad", "timestamp": "100", "nonce": "200"},
        content="<xml><Encrypt>bad</Encrypt></xml>",
    )
    assert response.status_code == 403
    assert events == []
