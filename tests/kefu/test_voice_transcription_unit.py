"""
Offline tests for core/voice_transcription.py: AMR validation and local
duration (the D6 60-second cap must be enforced before any paid call), the
real ffmpeg AMR → WAV conversion, keyword sanitization, and how provider
responses are classified retryable vs terminal. No network: the OpenAI
request is faked at requests.post.
"""
import pytest

from core import voice_transcription as vt


def _amr(frames: int, frame_type: int = 7) -> bytes:
    """Synthetic AMR-NB: frame_type 7 (12.2 kbps) = 1 header byte + 31 payload bytes."""
    header = bytes([(frame_type << 3) | 0x04])
    return vt.AMR_MAGIC + (header + bytes(vt._AMR_NB_FRAME_BYTES[frame_type])) * frames


# ── AMR validation ───────────────────────────────────────────────────────────

def test_duration_is_computed_from_frame_count():
    assert vt.amr_duration_ms(_amr(250)) == 5000
    assert vt.amr_duration_ms(_amr(246, frame_type=4)) == 4920  # a different bit rate


def test_non_amr_is_terminal():
    with pytest.raises(vt.VoiceTerminal) as err:
        vt.amr_duration_ms(b"RIFF....WAVEfmt ")
    assert err.value.reason == "not_amr"


def test_truncated_or_unknown_frames_are_terminal():
    with pytest.raises(vt.VoiceTerminal):
        vt.amr_duration_ms(_amr(10)[:-5])  # truncated final frame
    with pytest.raises(vt.VoiceTerminal):
        vt.amr_duration_ms(vt.AMR_MAGIC + bytes([(9 << 3) | 0x04]) + bytes(40))  # frame type 9


def test_sixty_second_cap_is_enforced_locally():
    assert vt.validate_amr(_amr(3000)) == 60_000
    with pytest.raises(vt.VoiceTerminal) as err:
        vt.validate_amr(_amr(3001))
    assert err.value.reason == "too_long"


def test_real_conversion_to_16khz_mono_wav():
    wav = vt.amr_to_wav(_amr(250))
    assert wav[:4] == b"RIFF" and wav[8:12] == b"WAVE"
    assert abs((len(wav) - 44) / 32000 - 5.0) < 0.05  # 16 kHz × 2 bytes × 5 s


# ── Keyword hints ────────────────────────────────────────────────────────────

def test_keywords_drop_characters_that_reject_the_whole_request():
    raw = ["s2", "t<4", "x>y", "bad\nline", "c\rr", "", "  S4  ", "s2", "x" * 41, "ok\r\n"]
    # Surrounding whitespace (incl. a trailing CR/LF) is trimmed, not fatal;
    # an embedded <, >, CR or LF drops the keyword.
    assert vt.sanitize_keywords(raw) == ["s2", "S4", "ok"]


def test_keywords_are_capped():
    assert len(vt.sanitize_keywords([f"k{i}" for i in range(80)])) == vt.MAX_KEYWORDS


# ── Provider response classification ────────────────────────────────────────

class _Resp:
    def __init__(self, status, body=None, text=""):
        self.status_code = status
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError("no json")
        return self._body


def _fake_post(monkeypatch, response=None, exc=None, sink=None):
    def post(url, headers, data, files, timeout):
        if sink is not None:
            sink.update(url=url, data=data, files=files)
        if exc is not None:
            raise exc
        return response
    monkeypatch.setattr(vt.requests, "post", post)


def test_success_sends_model_hints_and_returns_text(monkeypatch):
    sent = {}
    _fake_post(monkeypatch, _Resp(200, {"text": " 送两箱 S2 去 JFK。 "}), sink=sent)
    text = vt.transcribe_wav(b"RIFF", api_key="k", model="gpt-transcribe", keywords=["s2"])
    assert text == "送两箱 S2 去 JFK。"
    assert sent["url"].endswith("/v1/audio/transcriptions")
    assert sent["data"]["model"] == "gpt-transcribe"
    assert sent["data"]["languages[]"] == ["zh-cn", "en"]
    assert sent["data"]["keywords[]"] == ["s2"]
    assert sent["files"]["file"][2] == "audio/wav"


@pytest.mark.parametrize("status", [429, 500, 503])
def test_throttling_and_server_errors_are_retryable(monkeypatch, status):
    _fake_post(monkeypatch, _Resp(status, text="busy"))
    with pytest.raises(vt.VoiceRetryable):
        vt.transcribe_wav(b"RIFF", api_key="k", model="m", keywords=[])


@pytest.mark.parametrize("status", [400, 401, 403, 404])
def test_client_errors_are_terminal(monkeypatch, status):
    _fake_post(monkeypatch, _Resp(status, text='{"error": "nope"}'))
    with pytest.raises(vt.VoiceTerminal) as err:
        vt.transcribe_wav(b"RIFF", api_key="k", model="m", keywords=[])
    assert err.value.reason == "provider_rejected"


def test_network_failure_is_retryable(monkeypatch):
    _fake_post(monkeypatch, exc=vt.requests.ConnectionError("reset"))
    with pytest.raises(vt.VoiceRetryable):
        vt.transcribe_wav(b"RIFF", api_key="k", model="m", keywords=[])


def test_transcribe_amr_rejects_long_audio_before_any_request(monkeypatch):
    _fake_post(monkeypatch, exc=AssertionError("must not call the provider"))
    with pytest.raises(vt.VoiceTerminal) as err:
        vt.transcribe_amr(_amr(3001), api_key="k", model="m", keywords=[])
    assert err.value.reason == "too_long"
