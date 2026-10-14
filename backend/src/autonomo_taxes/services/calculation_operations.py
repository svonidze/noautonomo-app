"""Current dashboard operation shared by CLI and HTTP."""
from datetime import date
from pathlib import Path
from ..ledger_db import open as open_ledger_db
from ..dashboard_service import build_dashboard_from_db

def dashboard(*, database: Path, period: str, as_of: date, out_dir: Path, xolo_modelo130_calculations: Path | None = None):
    from ..current_quarter import (
        add_xolo_comparison,
        load_xolo_modelo130_forecast,
        write_current_quarter_dashboard,
    )

    with open_ledger_db(database, read_only=True) as db:
        dashboard = build_dashboard_from_db(db, period_key=period, as_of=as_of)

    if xolo_modelo130_calculations:
        add_xolo_comparison(
            dashboard,
            load_xolo_modelo130_forecast(
                xolo_modelo130_calculations,
                period=period,
            ),
        )
    outputs = write_current_quarter_dashboard(dashboard, out_dir)
    result = (
        {
            "period": period,
            "filing_assessment_performed": False,
            "filing_ready": False,
            "submission_ready": False,
            "outputs": {key: str(path.resolve()) for key, path in outputs.items()},
            "blocking_item_count": len(dashboard["blocking_items"]),
            "expected_item_count": len(dashboard["expected_items"]),
        }
    )
    return result, 0
