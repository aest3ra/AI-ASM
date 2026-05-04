from pathlib import Path

from sqlmodel import Session, select

from ai_asm.storage.db import Endpoint, Parameter, Scan, open_db


def test_create_and_query(tmp_path: Path):
    engine = open_db(tmp_path / "test.db")
    with Session(engine) as s:
        scan = Scan(target="https://example.com")
        s.add(scan)
        s.commit()
        s.refresh(scan)

        ep = Endpoint(
            scan_id=scan.id,
            method="GET",
            host="example.com",
            path_template="/users/{id}",
            sample_url="https://example.com/users/42",
        )
        s.add(ep)
        s.commit()
        s.refresh(ep)

        s.add(Parameter(
            endpoint_id=ep.id, location="query", name="page",
            type_inferred="int", sample_values_json="[1,2]",
        ))
        s.commit()

        rows = s.exec(select(Endpoint)).all()
        assert len(rows) == 1
        assert rows[0].path_template == "/users/{id}"
