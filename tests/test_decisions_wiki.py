"""실제 wiki(기준 커밋)로 5단계 완료 기준을 확인한다. 캐시가 없으면 건너뛴다."""

from pathlib import Path

import pytest

from tka.config import load_config
from tka.decisions.table import build_table, count_log_rows
from tka.ingest.fetch import checkout_problem, source_dir

ROOT = Path(__file__).parent.parent
CONFIG = load_config(ROOT / "config" / "ktb13.yaml")
WIKI = CONFIG.sources[0]
DIRECTORY = source_dir(CONFIG, ROOT, WIKI)

pytestmark = pytest.mark.skipif(
    checkout_problem(DIRECTORY, WIKI) is not None if DIRECTORY.exists() else True,
    reason="wiki를 기준 커밋으로 받아 두지 않았다 (python -m tka.ingest fetch)",
)


def test_decision_table_matches_the_log():
    table = build_table(CONFIG, ROOT)
    log = (DIRECTORY / table.log_path).read_text(encoding="utf-8")

    assert len(table.decisions) == count_log_rows(log) == 17
    assert table.problems == ()


def test_links_resolve_and_corrections_land():
    table = build_table(CONFIG, ROOT)

    assert sum(1 for d in table.decisions if d.detail_path) == 15
    corrected = {d.date: d.replaces for d in table.decisions if d.replaces}
    assert set(corrected) == {"2026-09-21", "2026-09-24", "2026-09-15"}
