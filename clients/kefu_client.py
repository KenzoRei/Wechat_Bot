"""HTTP client for the WeChat Kefu APIs used by the staff copilot."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import requests


API_BASE = "https://qyapi.weixin.qq.com"
TOKEN_ERROR_CODES = {40014, 42001, 42007}
QUOTA_ERROR_CODES = {95001}
WINDOW_ERROR_CODES = {95002, 95018}


class KefuAPIError(RuntimeError):
    def __init__(self, errcode: int, errmsg: str):
        super().__init__(f"Kefu API error {errcode}: {errmsg}")
        self.errcode = errcode
        self.errmsg = errmsg


class KefuWindowClosed(KefuAPIError):
    pass


class KefuQuotaExceeded(KefuAPIError):
    pass


class KefuTransportError(RuntimeError):
    pass


@dataclass(frozen=True)
class SyncPage:
    messages: list[dict[str, Any]]
    next_cursor: str
    has_more: bool


class KefuClient:
    def __init__(
        self,
        corp_id: str,
        secret: str,
        *,
        http: requests.Session | None = None,
        api_base: str = API_BASE,
        timeout: float = 15.0,
    ):
        self.corp_id = corp_id
        self.secret = secret
        self.http = http or requests.Session()
        self.api_base = api_base.rstrip("/")
        self.timeout = timeout
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        self._token_lock = threading.Lock()

    @staticmethod
    def _checked_json(response) -> dict[str, Any]:
        try:
            response.raise_for_status()
        except requests.RequestException as exc:
            raise KefuTransportError(f"Kefu HTTP request failed: {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise KefuTransportError("Kefu API returned non-JSON content") from exc
        errcode = int(data.get("errcode", 0))
        if errcode:
            errmsg = str(data.get("errmsg", "unknown error"))
            if errcode in WINDOW_ERROR_CODES:
                raise KefuWindowClosed(errcode, errmsg)
            if errcode in QUOTA_ERROR_CODES:
                raise KefuQuotaExceeded(errcode, errmsg)
            raise KefuAPIError(errcode, errmsg)
        return data

    def invalidate_token(self) -> None:
        with self._token_lock:
            self._access_token = None
            self._access_token_expires_at = 0.0

    def access_token(self) -> str:
        now = time.monotonic()
        with self._token_lock:
            if self._access_token and now < self._access_token_expires_at:
                return self._access_token
            response = self.http.get(
                f"{self.api_base}/cgi-bin/gettoken",
                params={"corpid": self.corp_id, "corpsecret": self.secret},
                timeout=self.timeout,
            )
            data = self._checked_json(response)
            token = data.get("access_token")
            if not token:
                raise RuntimeError("Kefu token response omitted access_token")
            expires_in = max(60, int(data.get("expires_in", 7200)))
            self._access_token = str(token)
            self._access_token_expires_at = time.monotonic() + expires_in - 60
            return self._access_token

    def _request(self, method: str, path: str, **kwargs) -> dict[str, Any]:
        base_params = dict(kwargs.pop("params", {}) or {})
        for attempt in range(2):
            token = self.access_token()
            params = dict(base_params)
            params["access_token"] = token
            response = self.http.request(
                method,
                f"{self.api_base}{path}",
                params=params,
                timeout=self.timeout,
                **kwargs,
            )
            try:
                return self._checked_json(response)
            except KefuAPIError as exc:
                if exc.errcode in TOKEN_ERROR_CODES and attempt == 0:
                    self.invalidate_token()
                    continue
                raise
        raise AssertionError("unreachable")

    def sync_messages(
        self,
        *,
        sync_token: str,
        cursor: str = "",
        open_kfid: str | None = None,
        limit: int = 1000,
    ) -> SyncPage:
        payload: dict[str, Any] = {
            "cursor": cursor,
            "token": sync_token,
            "limit": max(1, min(int(limit), 1000)),
            "voice_format": 0,
        }
        if open_kfid:
            payload["open_kfid"] = open_kfid
        data = self._request("POST", "/cgi-bin/kf/sync_msg", json=payload)
        messages = data.get("msg_list") or []
        if not isinstance(messages, list):
            raise RuntimeError("Kefu sync response msg_list is not a list")
        return SyncPage(
            messages=messages,
            next_cursor=str(data.get("next_cursor") or cursor),
            has_more=bool(data.get("has_more")),
        )

    def send_text(self, *, open_kfid: str, external_userid: str, text: str, msgid: str) -> str:
        if len(text.encode("utf-8")) > 2048:
            raise ValueError("Kefu text payload exceeds 2048 UTF-8 bytes")
        data = self._request(
            "POST",
            "/cgi-bin/kf/send_msg",
            json={
                "touser": external_userid,
                "open_kfid": open_kfid,
                "msgid": msgid,
                "msgtype": "text",
                "text": {"content": text},
            },
        )
        return str(data.get("msgid") or msgid)

    def upload_file(self, *, filename: str, content: bytes, content_type: str) -> str:
        data = self._request(
            "POST",
            "/cgi-bin/media/upload",
            params={"type": "file"},
            files={"media": (filename, content, content_type)},
        )
        media_id = data.get("media_id")
        if not media_id:
            raise RuntimeError("Kefu media upload response omitted media_id")
        return str(media_id)

    def send_file(self, *, open_kfid: str, external_userid: str, media_id: str, msgid: str) -> str:
        data = self._request(
            "POST",
            "/cgi-bin/kf/send_msg",
            json={
                "touser": external_userid,
                "open_kfid": open_kfid,
                "msgid": msgid,
                "msgtype": "file",
                "file": {"media_id": media_id},
            },
        )
        return str(data.get("msgid") or msgid)
