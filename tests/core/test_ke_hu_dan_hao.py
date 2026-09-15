"""
Direct, offline coverage for handlers/label/base.py's
_generate_ke_hu_dan_hao -- a pure function, no DB needed.
"""
from datetime import datetime, timezone

from handlers.label.base import _generate_ke_hu_dan_hao


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def test_ascii_display_name_passes_through():
    result = _generate_ke_hu_dan_hao({"display_name": "Simon", "serial_number": "REQ-20260101-000005"}, "F000225")
    assert result == f"ChatBot_F000225_Simon_{_today()}_005"


def test_chinese_display_name_strips_to_empty():
    """A purely Chinese nickname (e.g. a real Kefu staff display_name) has
    nothing ASCII left to keep -- the <user8> slot is simply empty, not a
    truncated/garbled Chinese fragment."""
    result = _generate_ke_hu_dan_hao({"display_name": "白小白", "serial_number": "REQ-20260101-000005"}, "F000225")
    assert result == f"ChatBot_F000225__{_today()}_005"


def test_mixed_script_name_keeps_the_ascii_portion():
    """Chinese is stripped BEFORE the 8-char truncation, so a mixed-script
    name keeps its meaningful ASCII part instead of losing it to an
    earlier truncation that happened to cut into the Chinese prefix."""
    result = _generate_ke_hu_dan_hao({"display_name": "客服Simon", "serial_number": "REQ-20260101-000005"}, "F000225")
    assert result == f"ChatBot_F000225_Simon_{_today()}_005"


def test_long_ascii_name_still_truncates_to_eight():
    result = _generate_ke_hu_dan_hao({"display_name": "Christopherson", "serial_number": "REQ-20260101-000005"}, "F000225")
    assert result == f"ChatBot_F000225_Christop_{_today()}_005"


def test_missing_serial_number_defaults_to_001():
    result = _generate_ke_hu_dan_hao({"display_name": "Simon"}, "F000225")
    assert result == f"ChatBot_F000225_Simon_{_today()}_001"
