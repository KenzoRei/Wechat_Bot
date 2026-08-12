from pathlib import Path

from sqlalchemy import text

from database import SessionLocal


MIGRATION = Path(__file__).parents[2] / "db" / "migrations" / "V14__kefu_deterministic_outbound_contract.sql"


def test_v14_catalog_migration_is_executable_and_idempotent_in_one_transaction():
    db = SessionLocal()
    try:
        sql = MIGRATION.read_text(encoding="utf-8")
        db.execute(text(sql))
        db.execute(text(sql))

        outbound = db.execute(text(
            "select input_schema from service_type where name='uchoice_outbound_request'"
        )).scalar_one()
        completion = db.execute(text(
            "select input_schema from service_type where name='confirm_outbound_completion'"
        )).scalar_one()

        assert "total boxes" in outbound["field_hints"]["sku_lines"]
        assert completion["optional"].count("destination_packing_lines") == 1
        assert "destination_packing_lines" in completion["field_hints"]
    finally:
        # This verifies the forward migration without changing the developer
        # database's migration level or catalog state.
        db.rollback()
        db.close()
