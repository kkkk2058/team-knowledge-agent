from pathlib import Path

import pytest
from helpers import git, make_golden, make_item, write_yaml

from tka.golden import (
    GoldenError,
    check_golden,
    load_golden,
    read_lines,
    render_review,
)

GOLDEN = Path(__file__).parent.parent / "eval" / "golden.yaml"


def _item(**overrides) -> dict:
    return make_item(**overrides)


def _golden(items: list[dict], commit: str = "abcdef1") -> dict:
    return make_golden(items, commit)


def _write(tmp_path: Path, data: dict) -> Path:
    return write_yaml(tmp_path / "golden.yaml", data)


# ── 형식 검사 ─────────────────────────────────────────────────────


def test_repo_golden_set_has_20_v0_items():
    golden = load_golden(GOLDEN)

    assert len(golden.for_version("v0")) == 20
    assert {i.type for i in golden.for_version("v0")} >= {"함정", "모름", "결정 이유"}


def test_evidence_lines_parse_single_and_range(tmp_path):
    data = _golden(
        [
            _item(evidence=[{"repo": "wiki", "path": "a.md", "lines": "3"}]),
            _item(id="g02", evidence=[{"repo": "wiki", "path": "a.md", "lines": "4-6"}]),
        ]
    )
    items = load_golden(_write(tmp_path, data)).items

    assert (items[0].evidence[0].start, items[0].evidence[0].end) == (3, 3)
    assert items[1].evidence[0].label() == "wiki/a.md:4-6"


@pytest.mark.parametrize("lines", ["0", "5-3", "abc", "3-", ""])
def test_bad_line_range_rejected(tmp_path, lines):
    data = _golden([_item(evidence=[{"repo": "wiki", "path": "a.md", "lines": lines}])])

    with pytest.raises(GoldenError, match=r"g01\.evidence\[0\]\.lines"):
        load_golden(_write(tmp_path, data))


def test_evidence_repo_must_have_source_commit(tmp_path):
    data = _golden([_item(evidence=[{"repo": "BE", "path": "a.java", "lines": "1"}])])

    with pytest.raises(GoldenError, match="source_commits에 없는 레포"):
        load_golden(_write(tmp_path, data))


def test_unknown_type_rejected(tmp_path):
    with pytest.raises(GoldenError, match="g01.type: '잡담'는 허용값이 아니다"):
        load_golden(_write(tmp_path, _golden([_item(type="잡담")])))


def test_duplicate_ids_rejected(tmp_path):
    with pytest.raises(GoldenError, match="id가 겹친다: g01"):
        load_golden(_write(tmp_path, _golden([_item(), _item()])))


def test_unknown_item_needs_absent_terms(tmp_path):
    data = _golden([_item(type="모름", evidence=[])])

    with pytest.raises(GoldenError, match="absent_terms로 없음을 확인"):
        load_golden(_write(tmp_path, data))


def test_answered_item_needs_evidence(tmp_path):
    with pytest.raises(GoldenError, match="근거\\(evidence\\)가 없다"):
        load_golden(_write(tmp_path, _golden([_item(evidence=[])])))


def test_absent_terms_only_for_unknown_items(tmp_path):
    data = _golden([_item(absent_terms=["x"])])

    with pytest.raises(GoldenError, match="absent_terms는 모름 문항에만"):
        load_golden(_write(tmp_path, data))


def test_absent_terms_must_be_a_list(tmp_path):
    data = _golden([_item(type="모름", evidence=[], absent_terms="Redux")])

    with pytest.raises(GoldenError, match="문자열 목록이어야 한다"):
        load_golden(_write(tmp_path, data))


def test_bad_source_commit_rejected(tmp_path):
    with pytest.raises(GoldenError, match="meta.source_commits.wiki: 커밋 형식이 아니다"):
        load_golden(_write(tmp_path, _golden([_item()], commit="main")))


# ── 원문 대조 ─────────────────────────────────────────────────────


def _check(tmp_path, config, commit, items):
    golden = load_golden(_write(tmp_path, _golden(items, commit)))
    return check_golden(golden, config, tmp_path)


def test_valid_evidence_has_no_problems(tmp_path, source):
    config, commit = source
    result = _check(
        tmp_path,
        config,
        commit,
        [_item(evidence=[{"repo": "wiki", "path": "docs/a.md", "lines": "3"}])],
    )

    assert result.problems == ()
    assert result.checked == ("wiki",)


def test_line_out_of_range(tmp_path, source):
    config, commit = source
    result = _check(
        tmp_path,
        config,
        commit,
        [_item(evidence=[{"repo": "wiki", "path": "docs/a.md", "lines": "3-4"}])],
    )

    assert [str(p) for p in result.problems] == [
        "[g01] wiki/docs/a.md:3-4: 줄 범위 밖이다 (파일은 3줄)"
    ]


def test_blank_evidence_line(tmp_path, source):
    config, commit = source
    result = _check(
        tmp_path,
        config,
        commit,
        [_item(evidence=[{"repo": "wiki", "path": "docs/a.md", "lines": "2"}])],
    )

    assert "근거 줄이 비어 있다" in str(result.problems[0])


def test_untracked_file_is_missing(tmp_path, source):
    config, commit = source
    result = _check(
        tmp_path,
        config,
        commit,
        [_item(evidence=[{"repo": "wiki", "path": "docs/untracked.md", "lines": "1"}])],
    )

    assert "기준 커밋에 없는 파일" in str(result.problems[0])


def test_v0_evidence_outside_include_rules(tmp_path, source):
    config, commit = source
    items = [
        _item(evidence=[{"repo": "wiki", "path": "backup/old.md", "lines": "1"}]),
        _item(
            id="g02",
            version="v3",
            evidence=[{"repo": "wiki", "path": "backup/old.md", "lines": "1"}],
        ),
    ]
    result = _check(tmp_path, config, commit, items)

    assert [p.item_id for p in result.problems] == ["g01"]  # v3 문항은 포함 규칙과 무관
    assert "포함 규칙 밖" in result.problems[0].message


def test_outside_scope_evidence_is_allowed_only_when_marked(tmp_path, source):
    config, commit = source
    outside = {"repo": "wiki", "path": "backup/old.md", "lines": "1", "outside_scope": True}
    wrongly_marked = {"repo": "wiki", "path": "docs/a.md", "lines": "3", "outside_scope": True}
    items = [
        _item(id="g01", evidence=[{"repo": "wiki", "path": "docs/a.md", "lines": "3"}, outside]),
        _item(id="g02", evidence=[wrongly_marked]),
    ]
    result = _check(tmp_path, config, commit, items)

    assert [str(p) for p in result.problems] == [
        "[g02] wiki/docs/a.md:3: outside_scope인데 포함 규칙 안이다"
    ]


def test_outside_scope_must_be_bool(tmp_path):
    ev = {"repo": "wiki", "path": "a.md", "lines": "1", "outside_scope": "yes"}

    with pytest.raises(GoldenError, match="outside_scope: true 또는 false"):
        load_golden(_write(tmp_path, _golden([_item(evidence=[ev])])))


def test_absent_term_found_in_included_file(tmp_path, source):
    config, commit = source
    items = [
        _item(id="g01", type="모름", evidence=[], absent_terms=["E5-SMALL"]),  # 대소문자 무시
        _item(id="g02", type="모름", evidence=[], absent_terms=["옛 설계"]),  # backup/은 포함 밖
    ]
    result = _check(tmp_path, config, commit, items)

    assert [str(p) for p in result.problems] == [
        "[g01] 모름 문항인데 'e5-small'이 wiki/docs/a.md:3에 있다"
    ]


def test_head_must_be_pinned_commit(tmp_path, source):
    config, commit = source
    repo = tmp_path / ".cache" / "sources" / "wiki"
    (repo / "docs" / "a.md").write_text("바뀜\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "next")

    result = _check(tmp_path, config, commit, [_item()])

    assert "기준 커밋이 아니다" in str(result.problems[0])
    assert result.checked == ()


def test_missing_checkout_tells_how_to_fetch(tmp_path, source):
    config, commit = source
    other = tmp_path / "other"
    other.mkdir()

    golden = load_golden(_write(tmp_path, _golden([_item()], commit)))
    result = check_golden(golden, config, other)

    assert "소스가 없다" in str(result.problems[0])
    assert "uv run python -m tka.ingest fetch" in str(result.problems[0])


def test_golden_commit_must_match_config_ref(tmp_path, source):
    config, _ = source
    result = _check(tmp_path, config, "1234567", [_item()])

    assert "설정 ref" in str(result.problems[0])


def test_repo_not_in_config_is_skipped(tmp_path, source):
    config, commit = source
    data = _golden([_item(evidence=[{"repo": "BE", "path": "x.java", "lines": "1"}])], commit)
    data["meta"]["source_commits"]["BE"] = "fedcba9"
    result = check_golden(load_golden(_write(tmp_path, data)), config, tmp_path)

    assert result.problems == ()
    assert result.skipped == {"BE": ("g01",)}


def test_review_quotes_evidence_lines(tmp_path, source):
    config, commit = source
    golden = load_golden(
        _write(
            tmp_path,
            _golden(
                [_item(evidence=[{"repo": "wiki", "path": "docs/a.md", "lines": "3"}])], commit
            ),
        )
    )

    sheet = render_review(golden, config, tmp_path)

    assert "`wiki/docs/a.md:3`" in sheet
    assert "    3  임베딩은 e5-small이다" in sheet


# ── 줄 번호 ───────────────────────────────────────────────────────


def test_read_lines_splits_only_on_newline(tmp_path):
    path = tmp_path / "x.md"
    path.write_bytes("첫 줄 같은 줄\r\n둘째 줄\x0c같은 줄\n".encode())

    assert read_lines(path) == ["첫 줄 같은 줄", "둘째 줄\x0c같은 줄"]


def test_bigset_items_have_b_ids_and_split(tmp_path):
    golden = load_golden(
        write_yaml(
            tmp_path / "g.yaml",
            make_golden([make_item(id="b001", type="현행 결정", status="자동 생성", split="test")]),
        )
    )

    assert (golden.items[0].id, golden.items[0].split) == ("b001", "test")
    assert golden.items[0].type == "현행 결정"


def test_bad_split_rejected(tmp_path):
    with pytest.raises(GoldenError, match="split"):
        load_golden(write_yaml(tmp_path / "g.yaml", make_golden([make_item(split="train")])))
