from pathlib import Path

import pytest
import yaml
from helpers import DECISION_LOG, decision_config, make_decision_repo

from tka.config import ConfigError, load_config
from tka.decisions.__main__ import main
from tka.decisions.table import (
    Correction,
    DecisionError,
    apply_corrections,
    build_table,
    count_log_rows,
    load_corrections,
    parse_decision_log,
)
from tka.ingest.fetch import FetchError

TRACKED = {"docs/ai/spec.md", "docs/ai/design.md", ".agents/team.md"}


def _parse(log: str = DECISION_LOG):
    return parse_decision_log(log, log_path="docs/dec/log.md", year=2026, tracked=TRACKED)


def _line_of(log: str, needle: str) -> int:
    return next(n for n, line in enumerate(log.split("\n"), 1) if needle in line)


# ── 로그 파싱 ─────────────────────────────────────────────────────


def test_rows_become_decisions_with_year_and_resolved_links():
    decisions, problems = _parse()

    assert problems == []
    assert [d.date for d in decisions] == ["2026-09-28", "2026-09-21", "2026-09-17", "2026-09-10"]
    first = decisions[0]
    assert (first.part, first.text, first.affected) == (
        "AI",
        "④ 피드는 GET으로 부른다",
        ("AI", "BE"),
    )
    assert (first.detail_path, first.detail_title) == ("docs/ai/spec.md", "모델 API 명세")
    assert first.line == _line_of(DECISION_LOG, "09-28")
    assert decisions[3].detail_path == ".agents/team.md"  # ../../ 도 레포 루트 기준으로 푼다


def test_cell_without_link_keeps_its_text_and_code_is_kept():
    decisions, _ = _parse()
    fs = decisions[2]

    assert (fs.detail_path, fs.detail_note) == (None, "형식 변경 시 이 로그에 추가")
    assert fs.text == "공통 응답 `{message, data}`"
    assert decisions[3].affected == ()  # "-"는 영향 파트 없음


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        ("| 9/21 | AI | x | AI | - |", "날짜가 MM-DD가 아니다: '9/21'"),
        ("| 09-21 | AI | x | AI | [a](../ai/없음.md) |", "상세 링크가 실제 파일로 풀리지 않는다"),
        ("| 09-21 | AI | x | AI | [a](../../../밖.md) |", "상세 링크가 레포 밖을 가리킨다"),
        ("| 09-21 | AI |", "파트나 결정 칸이 비어 있다"),
    ],
)
def test_bad_rows_are_reported_not_fatal(row, problem):
    log = DECISION_LOG + row + "\n"
    decisions, problems = _parse(log)

    assert len(problems) == 1 and problem in problems[0]
    row_line = log.split("\n").index(row) + 1
    assert problems[0].startswith(f"docs/dec/log.md:{row_line}")
    assert len(decisions) in (4, 5)  # 링크 문제 행은 결정으로 남는다


def test_korean_link_is_decoded():
    log = DECISION_LOG + "| 09-21 | AI | x | AI | [로그](../ai/개발-로그.md) |\n"
    decisions, problems = parse_decision_log(
        log, log_path="docs/dec/log.md", year=2026, tracked=TRACKED | {"docs/ai/개발-로그.md"}
    )

    assert problems == []
    assert decisions[-1].detail_path == "docs/ai/개발-로그.md"


def test_log_without_decision_table_is_an_error():
    with pytest.raises(DecisionError, match="머리행이 날짜 | 파트"):
        _parse("| a | b |\n|---|---|\n| 1 | 2 |\n")


def test_count_log_rows():
    assert count_log_rows(DECISION_LOG) == 4


# ── 보정 ──────────────────────────────────────────────────────────


def _correct(corrections, lines_of=None):
    decisions, _ = _parse()
    return apply_corrections(decisions, corrections, lines_of=lines_of or {"docs/ai/design.md": 3})


def test_correction_attaches_replaced_decision_and_old_text():
    fixed, problems = _correct(
        [Correction("09-21", "LangChain", "V1 미도입", ("docs/ai/design.md:2",), None)]
    )

    assert problems == []
    assert fixed[1].replaces == "V1 미도입"
    assert fixed[1].old_text_at == ("docs/ai/design.md:2",)
    assert fixed[1].status == "현행"


def test_superseded_by_marks_status():
    fixed, problems = _correct([Correction("09-10", "Recreate", None, (), ("09-28", "피드"))])

    assert problems == []
    assert (fixed[3].status, fixed[3].superseded_by) == ("대체됨", fixed[0].line)


@pytest.mark.parametrize(
    ("correction", "problem"),
    [
        (Correction("09-22", "LangChain", "x", (), None), "맞는 로그 행이 없다"),
        (Correction("09-21", "LangChain", "x", ("docs/ai/design.md:9",), None), "줄 범위 밖이다"),
        (Correction("09-21", "LangChain", "x", ("docs/없음.md:1",), None), "기준 커밋에 없는 파일"),
        (Correction("09-21", "LangChain", "x", ("design.md",), None), "'경로:줄' 형식이 아니다"),
        (
            Correction("09-10", "배포", None, (), ("09-01", "x")),
            "superseded_by: 맞는 로그 행이 없다",
        ),
    ],
)
def test_bad_corrections_are_reported(correction, problem):
    _, problems = _correct([correction])

    assert len(problems) == 1 and problem in problems[0]


def test_ambiguous_correction_is_reported():
    log = DECISION_LOG + "| 09-21 | AI | LangChain 추가 결정 | AI | - |\n"
    decisions, _ = _parse(log)

    _, problems = apply_corrections(
        decisions, [Correction("09-21", "LangChain", "x", (), None)], lines_of={}
    )

    assert problems == ["보정 09-21 'LangChain': 맞는 로그 행이 2개다"]


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("corrections: [{date: '9-21', contains: x}]", "date\\(MM-DD\\)와 contains"),
        ("corrections: [{date: '09-21', contains: x, old_text_at: a.md:1}]", "'경로:줄' 목록"),
        (
            "corrections: [{date: '09-21', contains: x, superseded_by: {date: '09-22'}}]",
            "superseded_by",
        ),
        ("corrections: {a: 1}", "corrections는 목록"),
        ("corrections: [", "YAML 문법 오류"),
    ],
)
def test_bad_corrections_file(tmp_path, content, message):
    path = tmp_path / "s.yaml"
    path.write_text(content, encoding="utf-8")

    with pytest.raises(DecisionError, match=message):
        load_corrections(path)


# ── 조립·설정·CLI ─────────────────────────────────────────────────


@pytest.fixture
def pinned(tmp_path):
    repo = tmp_path / ".cache" / "sources" / "wiki"
    commit = make_decision_repo(repo)
    return decision_config(tmp_path, commit), commit


def test_build_table_from_cache(tmp_path, pinned):
    config, commit = pinned

    table = build_table(config, tmp_path)

    assert (table.commit, table.problems) == (commit, ())
    assert len(table.decisions) == 4
    assert table.decisions[1].replaces == "V1은 LangChain을 쓰지 않는다"
    line = table.decisions[1].line
    assert table.citation(table.decisions[1]) == f"wiki/docs/dec/log.md:{line}@{commit[:7]}"


def test_build_table_without_fetch_tells_how(tmp_path):
    config = decision_config(tmp_path, "abcdef1")

    with pytest.raises(FetchError, match="python -m tka.ingest fetch"):
        build_table(config, tmp_path)


def test_check_command_passes_on_clean_table(tmp_path, pinned, capsys):
    config, commit = pinned
    config_file = _write_config(tmp_path, commit)

    code = main(["check", "--config", str(config_file), "--root", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert "결정 표 4행 / 로그 4행" in out and "문제 0건" in out


def test_find_command_prints_evidence(tmp_path, pinned, capsys):
    _, commit = pinned
    config_file = _write_config(tmp_path, commit)

    main(["find", "피드는", "--config", str(config_file), "--root", str(tmp_path)])

    assert "[현행] 2026-09-28 AI — ④ 피드는 GET으로 부른다" in capsys.readouterr().out


def _write_config(root: Path, commit: str) -> Path:
    path = root / "c.yaml"
    path.write_text(
        yaml.safe_dump(
            {
                "team": "t",
                "decision_log_year": 2026,
                "paths": {"cache_dir": ".cache/sources", "index_path": "data/index.sqlite"},
                "sources": [
                    {"name": "wiki", "repo": "org/wiki", "ref": commit[:7], "include": ["docs/"]}
                ],
                "decisions": {
                    "source": "wiki",
                    "log": "docs/dec/log.md",
                    "corrections": "config/supersedes.yaml",
                },
                "aliases": [["④", "feed", "피드"]],
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    return path


def test_team_config_has_decisions_and_aliases():
    config = load_config(Path(__file__).parent.parent / "config" / "ktb13.yaml")

    assert config.decisions is not None
    assert config.decisions.log == "docs/dec/000-decision-log.md"
    assert ("④", "recommendations/feed", "feed", "피드") in config.aliases


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"decisions": {"source": "없음", "log": "x.md"}}, "sources에 없는 이름"),
        ({"decisions": {"source": "wiki"}}, "decisions.log: 없다"),
        ({"aliases": [["혼자"]]}, "말 두 개 이상"),
        ({"aliases": "①"}, "aliases: 목록"),
    ],
)
def test_bad_decision_config(tmp_path, patch, message):
    base = yaml.safe_load(_write_config(tmp_path, "abcdef1").read_text(encoding="utf-8"))
    base.update(patch)
    path = tmp_path / "bad.yaml"
    path.write_text(yaml.safe_dump(base, allow_unicode=True), encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config(path)
