import pytest
import yaml
from helpers import git, make_golden, make_item, write_yaml

from tka.evaluation import (
    Citation,
    ResultError,
    SourceFiles,
    check_citations,
    dump_yaml,
    evaluate,
    extract_citations,
    load_run,
    load_scores,
    load_sources,
    render_summary,
    resolve_path,
    write_template,
)
from tka.golden import load_golden


def _golden(tmp_path, items, commit):
    return load_golden(write_yaml(tmp_path / "golden.yaml", make_golden(items, commit)))


# ── 인용 추출 ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("근거: `docs/a.md:3`", [("docs/a.md", 3, 3)]),
        ("docs/a.md:3-5 와 docs/a.md:3-5", [("docs/a.md", 3, 5)]),  # 중복 제거
        ("spec.md#L12", [("spec.md", 12, 12)]),
        ("spec.md#L12-L14", [("spec.md", 12, 14)]),
        ("a.md:3–5", [("a.md", 3, 5)]),  # 긴 줄표
        ("https://github.com/org/wiki/blob/4f6a6a6/docs/a.md#L3", [("docs/a.md", 3, 3)]),
        ("./docs/a.md:1", [("docs/a.md", 1, 1)]),
        (".agents/d.md:1", [(".agents/d.md", 1, 1)]),  # 앞의 점은 지우지 않는다
        ("docs/a.md:9-3", []),  # 거꾸로 된 범위
        ("버전 1.2:3 같은 문장", []),  # 확장자가 없으면 인용이 아니다
    ],
)
def test_extract_citations(text, expected):
    assert [(c.raw_path, c.start, c.end) for c in extract_citations(text)] == expected


def test_resolve_path_accepts_unique_suffix_only():
    tracked = {"docs/a.md", "x/spec.md", "y/spec.md", "docs/sub/only.md"}

    assert resolve_path("docs/a.md", tracked) == "docs/a.md"
    assert resolve_path("only.md", tracked) == "docs/sub/only.md"
    assert resolve_path("spec.md", tracked) is None  # 둘 중 무엇인지 모른다
    assert resolve_path("pec.md", tracked) is None  # 폴더 경계에서만 맞춘다


def test_resolve_path_matches_nfd_and_nfc_korean_names():
    import unicodedata

    nfd = unicodedata.normalize("NFD", "backup/개발-로그.md")
    tracked = {nfd}

    assert resolve_path("backup/개발-로그.md", tracked) == nfd  # 원래 경로를 돌려준다
    assert resolve_path("개발-로그.md", tracked) == nfd


# ── 인용 판정 ─────────────────────────────────────────────────────


def test_check_citations(tmp_path, source):
    config, commit = source
    repo = tmp_path / ".cache" / "sources" / "wiki"
    item = _golden(
        tmp_path,
        [make_item(evidence=[{"repo": "wiki", "path": "docs/sub/spec.md", "lines": "10"}])],
        commit,
    ).items[0]
    sources = {"wiki": SourceFiles(repo, set(git(repo, "ls-files").splitlines()))}
    citations = [
        Citation("spec.md", 12, 13),  # 근거에서 2줄 → 겹침으로 본다
        Citation("spec.md", 15, 15),  # 5줄 떨어짐 → 있지만 근거는 아님
        Citation("docs/a.md", 9, 9),  # 파일은 3줄
        Citation("nope.md", 1, 1),  # 없는 파일
    ]

    checks = check_citations(citations, item, sources)

    assert [(c.path, c.exists, c.hits_evidence) for c in checks] == [
        ("docs/sub/spec.md", True, True),
        ("docs/sub/spec.md", True, False),
        ("docs/a.md", False, False),
        (None, False, False),
    ]


def test_citation_through_symlinked_folder(tmp_path, source):
    config, commit = source
    repo = tmp_path / ".cache" / "sources" / "wiki"
    (repo / ".claude").mkdir()
    (repo / ".claude" / "skills").symlink_to("../.agents")  # 폴더 링크
    git(repo, "add", ".claude")
    git(repo, "commit", "-q", "-m", "link")
    item = _golden(tmp_path, [make_item()], commit).items[0]
    sources = {"wiki": SourceFiles(repo, set(git(repo, "ls-files").splitlines()))}

    checks = check_citations([Citation(".claude/skills/d.md", 1, 1)], item, sources)

    assert (checks[0].path, checks[0].exists) == (".agents/d.md", True)


def test_load_sources_stops_when_checkout_moved(tmp_path, source):
    config, commit = source
    repo = tmp_path / ".cache" / "sources" / "wiki"
    (repo / "docs" / "a.md").write_text("바뀜\n", encoding="utf-8")
    git(repo, "commit", "-q", "-am", "next")

    with pytest.raises(ResultError, match="기준 커밋이 아니다"):
        load_sources(_golden(tmp_path, [make_item()], commit), config, tmp_path)


# ── 실행 결과와 요약 ──────────────────────────────────────────────


def _run_dir(tmp_path, answers, scores=None):
    run_dir = tmp_path / "eval" / "results" / "r1"
    run_dir.mkdir(parents=True)
    write_yaml(
        run_dir / "answers.yaml",
        {"run_id": "r1", "system": "test", "detail": {"모델": "m"}, "answers": answers},
    )
    if scores is not None:
        write_yaml(run_dir / "scores.yaml", {"items": scores})
    return run_dir


def test_summary_counts_verdicts_citations_and_types(tmp_path, source):
    config, commit = source
    golden = _golden(
        tmp_path,
        [
            make_item(id="g01", evidence=[{"repo": "wiki", "path": "docs/a.md", "lines": "3"}]),
            make_item(
                id="g02",
                type="함정",
                evidence=[{"repo": "wiki", "path": "docs/b.md", "lines": "1"}],
            ),
            make_item(id="g03", type="모름", evidence=[], absent_terms=["토스페이먼츠"]),
        ],
        commit,
    )
    run_dir = _run_dir(
        tmp_path,
        [
            {
                "id": "g01",
                "answer": "e5-small (docs/a.md:3)",
                "meta": {"cost_usd": 0.2, "duration_ms": 20000},
            },
            {
                "id": "g02",
                "answer": "틀린 답 docs/a.md:99",
                "meta": {"cost_usd": 0.1, "duration_ms": 10000},
            },
            {"id": "g03", "answer": "", "error": "시간 초과"},
        ],
        [
            {"id": "g01", "verdict": "정답"},
            {"id": "g02", "verdict": "부분", "failure": "폐기 내용 인용"},
            {"id": "g03", "verdict": "오답", "failure": "근거 못 찾음"},
        ],
    )

    results = evaluate(golden, load_run(run_dir), load_scores(run_dir), config, tmp_path)
    summary = render_summary(load_run(run_dir), results)

    assert "| 정답률 (정답 1, 부분 0.5) | 50% (1.5/3) |" in summary
    assert "| 인용 정확도 (인용한 파일·줄이 실제로 있음) | 50% (1/2) |" in summary
    assert "| 근거 적중 (골든셋 근거 줄을 인용한 문항) | 50% (1/2) |" in summary
    assert "| 답이 없는 문항 (오류 포함) | 1 |" in summary
    assert "| 함정 | 50% (0.5/1) |" in summary
    assert "| g03 | 모름 | 오답 | 0/0 | — |" in summary
    assert "| 질문당 평균 비용 | $0.1500 |" in summary  # 오류 문항은 빼고 평균
    assert "| 질문당 평균 응답 시간 | 15.0초 |" in summary
    assert "| 폐기 내용 인용 | 1 |" in summary and "| 근거 못 찾음 | 1 |" in summary


def test_summary_before_scoring(tmp_path, source):
    config, commit = source
    golden = _golden(tmp_path, [make_item()], commit)
    run_dir = _run_dir(tmp_path, [{"id": "g01", "answer": "답"}])

    summary = render_summary(
        load_run(run_dir), evaluate(golden, load_run(run_dir), {}, config, tmp_path)
    )

    assert "| 채점한 문항 | 0/1 |" in summary
    assert "| g01 | 명세 값 | 미채점 |" in summary


@pytest.mark.parametrize(
    ("score", "message"),
    [
        ({"id": "g01", "verdict": "오답", "failure": "모름"}, "failure는 근거 못 찾음"),
        ({"id": "g01", "verdict": "정답", "failure": "지어냄"}, "정답에는 failure를 적지 않는다"),
        ({"verdict": "정답"}, "id가 없다"),
    ],
)
def test_bad_failure_rejected(tmp_path, score, message):
    run_dir = _run_dir(tmp_path, [], [score])

    with pytest.raises(ResultError, match=message):
        load_scores(run_dir)


def test_bad_verdict_rejected(tmp_path):
    run_dir = _run_dir(tmp_path, [], [{"id": "g01", "verdict": "맞음"}])

    with pytest.raises(ResultError, match="verdict는 정답·부분·오답"):
        load_scores(run_dir)


def test_template_lists_prompts_and_refuses_overwrite(tmp_path):
    golden = _golden(tmp_path, [make_item(question="임베딩 모델은?")], "abcdef1")
    run_dir = tmp_path / "r"

    path = write_template(run_dir, golden, "deepwiki")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))

    assert data["system"] == "deepwiki"
    assert data["answers"][0]["question"].startswith("임베딩 모델은?\n\n이 레포의 문서를 근거로")
    with pytest.raises(ResultError, match="이미 있다"):
        write_template(run_dir, golden, "deepwiki")


def test_dump_yaml_writes_multiline_as_block():
    text = dump_yaml({"answer": "첫 줄\n둘째 줄"})

    assert "answer: |" in text
    assert yaml.safe_load(text) == {"answer": "첫 줄\n둘째 줄"}


def test_item_problem_is_left_out_of_accuracy(tmp_path, source):
    config, commit = source
    golden = _golden(tmp_path, [make_item(id="g01"), make_item(id="g02")], commit)
    run_dir = _run_dir(
        tmp_path,
        [{"id": "g01", "answer": "답"}, {"id": "g02", "answer": "답"}],
        [
            {"id": "g01", "verdict": "정답"},
            {"id": "g02", "verdict": "오답", "failure": "근거 못 찾음", "item_problem": True},
        ],
    )

    summary = render_summary(
        load_run(run_dir),
        evaluate(golden, load_run(run_dir), load_scores(run_dir), config, tmp_path),
    )

    assert "| 정답률 (정답 1, 부분 0.5) | 100% (1/1) |" in summary
    assert "| 문항 오류로 뺀 문항 | 1 |" in summary
    assert "| g02 | 명세 값 | 미채점 |" in summary


def test_item_problem_must_be_bool(tmp_path):
    run_dir = _run_dir(tmp_path, [], [{"id": "g01", "verdict": "정답", "item_problem": "yes"}])

    with pytest.raises(ResultError, match="item_problem은 true 또는 false다"):
        load_scores(run_dir)


def test_evaluate_filters_by_split_and_ids(tmp_path, source):
    config, commit = source
    golden = _golden(
        tmp_path,
        [
            make_item(id="b001", split="dev"),
            make_item(id="b002", split="test"),
            make_item(id="b003", split="test"),
        ],
        commit,
    )
    run = load_run(
        _run_dir(tmp_path, [{"id": i, "answer": "답"} for i in ("b001", "b002", "b003")])
    )

    test_only = evaluate(golden, run, {}, config, tmp_path, split="test")
    picked = evaluate(golden, run, {}, config, tmp_path, split="test", ids={"b003"})

    assert [r.item.id for r in test_only] == ["b002", "b003"]
    assert [r.item.id for r in picked] == ["b003"]


def test_other_scores_file_can_be_read(tmp_path):
    run_dir = _run_dir(tmp_path, [])
    write_yaml(run_dir / "scores-llm.yaml", {"items": [{"id": "g01", "verdict": "부분"}]})

    assert load_scores(run_dir) == {}
    assert load_scores(run_dir, "scores-llm.yaml")["g01"].verdict == "부분"
