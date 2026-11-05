"""Guards for the upsert_address -> add_address rename (V43).

Static: a missed string comparison fails silently (a name check simply
stops matching), so assert no live source still uses the old name and the
handler registry is keyed by the new step type.
"""
import pathlib
import re

from handlers.registry import HANDLER_REGISTRY

ROOT = pathlib.Path(__file__).resolve().parents[2]
_OLD = re.compile(r"upsert_address|UpsertAddress")
_LIVE_DIRS = ("ai", "api", "clients", "core", "handlers", "jobs", "middleware", "models")


def test_registry_uses_new_step_type_only():
    assert "add_address" in HANDLER_REGISTRY
    assert "upsert_address" not in HANDLER_REGISTRY
    assert HANDLER_REGISTRY["add_address"].__name__ == "AddAddressHandler"


def test_no_live_source_uses_old_service_name():
    offenders = []
    for d in _LIVE_DIRS:
        for path in (ROOT / d).rglob("*.py"):
            for n, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                if _OLD.search(line):
                    offenders.append(f"{path.relative_to(ROOT)}:{n}")
    for path in ROOT.glob("*.py"):
        if _OLD.search(path.read_text(encoding="utf-8", errors="replace")):
            offenders.append(path.name)
    assert not offenders, offenders


def test_v43_migration_is_transaction_safe_and_renames_all_three_tables():
    sql = (ROOT / "db" / "migrations" / "V43__rename_upsert_address_to_add_address.sql").read_text(encoding="utf-8")
    assert "COMMIT" not in sql.upper().replace("NO COMMIT", "")
    for table, column in (("service_type", "name"), ("workflow", "name"), ("workflow_step", "step_type")):
        assert f"UPDATE {table}" in sql and column in sql
    assert "RAISE EXCEPTION" in sql
