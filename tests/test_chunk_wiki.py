"""실제 wiki(기준 커밋)로 4단계 완료 기준을 확인한다. 캐시가 없으면 건너뛴다.

uv run python -m tka.ingest fetch   # 먼저 받아 둔다
"""

import re
from pathlib import Path

import pytest

from tka.config import load_config
from tka.golden import load_golden
from tka.ingest.chunk import chunk_files, coverage_problems
from tka.ingest.fetch import checkout_problem, source_dir
from tka.ingest.files import select_files

ROOT = Path(__file__).parent.parent
CONFIG = load_config(ROOT / "config" / "ktb13.yaml")
WIKI = CONFIG.sources[0]
DIRECTORY = source_dir(CONFIG, ROOT, WIKI)

pytestmark = pytest.mark.skipif(
    checkout_problem(DIRECTORY, WIKI) is not None if DIRECTORY.exists() else True,
    reason="wiki를 기준 커밋으로 받아 두지 않았다 (python -m tka.ingest fetch)",
)


@pytest.fixture(scope="module")
def wiki():
    files = select_files(CONFIG, ROOT, WIKI)
    docs, chunks = chunk_files(files, DIRECTORY)
    return files, docs, chunks


def _of(chunks, path):
    return [c for c in chunks if c.path == path]


def test_ai5_callout_is_on_every_chunk(wiki):
    _, _, chunks = wiki
    ai5 = _of(chunks, "docs/ai/5-context-augmentation/design.md")

    assert ai5
    assert all(c.callout and "book_passage" in c.callout for c in ai5)


def test_fs2_is_split_into_40_endpoints(wiki):
    _, _, chunks = wiki
    endpoints = [c for c in _of(chunks, "docs/fs/2-api/spec.md") if c.in_details]

    assert len(endpoints) == 40
    assert all(
        re.match(r"(GET|POST|PUT|PATCH|DELETE) /api/", c.heading_path[-1]) for c in endpoints
    )


def test_fs1_has_28_tables(wiki):
    _, _, chunks = wiki
    # 긴 테이블은 라벨(**컬럼 설명**)에서 더 나뉘므로 경로 안의 "users · 유저" 꼴 제목을 센다
    tables = {
        title
        for c in _of(chunks, "docs/fs/1-table-spec/spec.md")
        if c.in_details
        for title in c.heading_path
        if re.match(r"[a-z_]+ · ", title)
    }

    assert len(tables) == 28


def test_no_body_line_is_lost_or_duplicated(wiki):
    files, _, chunks = wiki
    problems = [
        f"{f.path}: {p}"
        for f in files
        for p in coverage_problems(
            (DIRECTORY / f.path).read_text(encoding="utf-8"), _of(chunks, f.path)
        )
    ]

    assert problems == []


def test_golden_evidence_sits_inside_one_chunk(wiki):
    _, _, chunks = wiki
    golden = load_golden(ROOT / "eval" / "golden.yaml")
    outside = []
    for item in golden.for_version("v0"):
        for ev in item.evidence:
            if ev.outside_scope:
                continue
            if not any(
                c.path == ev.path and c.start_line <= ev.start and ev.end <= c.end_line
                for c in chunks
            ):
                outside.append(f"{item.id} {ev.label()}")

    assert outside == []


def test_text_is_normalized(wiki):
    _, _, chunks = wiki

    assert not any(" " in c.text for c in chunks)
