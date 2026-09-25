"""
Speech → text for Kefu voice messages (audio-input plan rev 6, step 4).

Pure audio + provider logic, no database: validate AMR-NB bytes, compute
their duration locally (needed *before* any paid call to enforce the D6
60-second cap), decode AMR → WAV with a pip-bundled ffmpeg, and transcribe
with OpenAI (D1; `gpt-transcribe` by default -- the older transcription
models are scheduled for removal on 2027-02-26).

Every failure is classified, because the queue reacts differently:
- VoiceRetryable: worth retrying (network, provider 5xx/429, timeouts).
- VoiceTerminal: retrying can't help (bad/expired media, too long, the
  provider rejecting the request). `reason` picks the staff-facing reply.
Never logs audio bytes or transcripts.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from dataclasses import dataclass

import requests

AMR_MAGIC = b"#!AMR\n"
MAX_DURATION_MS = 60_000          # D6: matches WeChat's own voice-message cap
MAX_AUDIO_BYTES = 3 * 1024 * 1024  # malformed-file guard; 60 s of AMR-NB is ~100 KB
# AMR-NB payload bytes per frame type (excluding the 1-byte frame header);
# 8 = SID (comfort noise), 15 = NO_DATA. Every frame is 20 ms.
_AMR_NB_FRAME_BYTES = {0: 12, 1: 13, 2: 15, 3: 17, 4: 19, 5: 20, 6: 26, 7: 31, 8: 5, 15: 0}
_FRAME_MS = 20

PROMPT = "仓库出入库对话，涉及托、箱、出库、入库、仓库代码和商品编码。"
LANGUAGES = ("zh-cn", "en")
MAX_KEYWORDS = 50
MAX_KEYWORD_LENGTH = 40


class VoiceError(Exception):
    pass


class VoiceRetryable(VoiceError):
    pass


class VoiceTerminal(VoiceError):
    """reason: 'media_unavailable' | 'not_amr' | 'too_long' | 'provider_rejected'."""

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        super().__init__(f"{reason}: {detail}" if detail else reason)


@dataclass(frozen=True)
class Transcript:
    text: str
    provider: str
    duration_ms: int


def amr_duration_ms(data: bytes) -> int:
    """Validates AMR-NB framing and returns the duration. Raises
    VoiceTerminal('not_amr') for anything that isn't a well-formed AMR-NB
    stream (wrong magic, unknown frame type, truncated final frame)."""
    if not data.startswith(AMR_MAGIC):
        raise VoiceTerminal("not_amr", f"magic {data[:6]!r}")
    pos, frames = len(AMR_MAGIC), 0
    while pos < len(data):
        frame_type = (data[pos] >> 3) & 0x0F
        if frame_type not in _AMR_NB_FRAME_BYTES:
            raise VoiceTerminal("not_amr", f"frame type {frame_type} at byte {pos}")
        pos += 1 + _AMR_NB_FRAME_BYTES[frame_type]
        frames += 1
    if pos != len(data):
        raise VoiceTerminal("not_amr", "truncated final frame")
    return frames * _FRAME_MS


def validate_amr(data: bytes) -> int:
    """All pre-provider checks (D6): size guard, AMR framing, 60 s cap."""
    if len(data) > MAX_AUDIO_BYTES:
        raise VoiceTerminal("too_long", f"{len(data)} bytes")
    duration_ms = amr_duration_ms(data)
    if duration_ms > MAX_DURATION_MS:
        raise VoiceTerminal("too_long", f"{duration_ms} ms")
    return duration_ms


def amr_to_wav(data: bytes) -> bytes:
    """Decode AMR-NB to 16 kHz mono PCM WAV (lossless from the decoded
    audio -- no second lossy encode). A decode failure on already-validated
    AMR is not something a retry fixes, so it's terminal."""
    import imageio_ffmpeg

    ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    with tempfile.TemporaryDirectory() as tmp:
        src, dst = os.path.join(tmp, "in.amr"), os.path.join(tmp, "out.wav")
        with open(src, "wb") as f:
            f.write(data)
        try:
            proc = subprocess.run(
                [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", src, "-ar", "16000", "-ac", "1", dst],
                capture_output=True, text=True, timeout=60,
            )
        except subprocess.TimeoutExpired as exc:
            raise VoiceRetryable("ffmpeg timed out") from exc
        if proc.returncode != 0:
            raise VoiceTerminal("not_amr", f"ffmpeg exit {proc.returncode}: {proc.stderr[-300:]}")
        with open(dst, "rb") as f:
            return f.read()


def sanitize_keywords(candidates) -> list[str]:
    """gpt-transcribe rejects the whole request if any keyword contains
    '<', '>', CR or LF; keep hints short, distinct and bounded."""
    seen, out = set(), []
    for raw in candidates:
        kw = str(raw or "").strip()
        if not kw or len(kw) > MAX_KEYWORD_LENGTH or any(c in kw for c in "<>\r\n") or kw in seen:
            continue
        seen.add(kw)
        out.append(kw)
        if len(out) == MAX_KEYWORDS:
            break
    return out


def transcribe_wav(wav: bytes, *, api_key: str, model: str, keywords: list[str], timeout: float = 60.0) -> str:
    """One OpenAI transcription request. Multipart array syntax (`key[]`)
    for languages/keywords was confirmed by the production smoke test."""
    data: dict = {"model": model, "prompt": PROMPT, "languages[]": list(LANGUAGES)}
    if keywords:
        data["keywords[]"] = keywords
    try:
        response = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key}"},
            data=data,
            files={"file": ("voice.wav", wav, "audio/wav")},
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise VoiceRetryable(f"transcription request failed: {type(exc).__name__}") from exc
    if response.status_code == 429 or response.status_code >= 500:
        raise VoiceRetryable(f"transcription HTTP {response.status_code}")
    if response.status_code != 200:
        # 4xx: bad request, permissions, unknown model -- a config/request
        # problem a retry won't fix. The body names the cause; no secrets in it.
        raise VoiceTerminal("provider_rejected", f"HTTP {response.status_code}: {response.text[:300]}")
    try:
        return str(response.json().get("text") or "").strip()
    except ValueError as exc:
        raise VoiceRetryable("transcription returned non-JSON") from exc


def transcribe_amr(data: bytes, *, api_key: str, model: str, keywords: list[str]) -> Transcript:
    duration_ms = validate_amr(data)
    wav = amr_to_wav(data)
    text = transcribe_wav(wav, api_key=api_key, model=model, keywords=sanitize_keywords(keywords))
    return Transcript(text=text, provider=f"openai:{model}", duration_ms=duration_ms)
