import pytest
from helpers import git, make_golden, make_item, write_yaml

from tka import judge
from tka.answer.llm import LLMError, LLMResult
from tka.evaluation import Answer, Score, SourceFiles, load_scores
from tka.golden import load_golden


@pytest.fixture
def setup(tmp_path, source):
    config, commit = source
    golden = load_golden(
        write_yaml(
            tmp_path / "golden.yaml",
            make_golden(
                [
                    make_item(
                        id="g01",
                        question="임베딩 모델은?",
                        expected="e5-small",
                        evidence=[{"repo": "wiki", "path": "docs/a.md", "lines": "3"}],
                        trap="옛 모델 이름과 헷갈린다",
                    ),
                    make_item(id="g02", type="모름", evidence=[], absent_terms=["토스페이먼츠"]),
                ],
                commit,
            ),
        )
    )
    repo = tmp_path / ".cache" / "sources" / "wiki"
    sources = {"wiki": SourceFiles(repo, set(git(repo, "ls-files").splitlines()))}
    return golden, sources


def _answer(item_id, text, sources=()):
    return Answer(item_id, text, None, {"sources": list(sources)})


def test_prompt_has_question_expected_note_evidence_and_answer(setup):
    golden, sources = setup

    prompt = judge.build_prompt(golden.items[0], _answer("g01", "e5-small이다"), sources)

    assert "[질문]\n임베딩 모델은?" in prompt
    assert "[기준 정답]\ne5-small" in prompt
    assert "[채점 메모]\n옛 모델 이름과 헷갈린다" in prompt
    assert "wiki/docs/a.md:3\n    1  # 제목" in prompt  # 근거 줄 앞뒤 2줄까지
    assert "    3  임베딩은 e5-small이다" in prompt
    assert prompt.endswith("[답]\ne5-small이다")


def test_failure_type_is_decided_by_code(setup):
    golden, sources = setup
    item, unknown = golden.items

    assert judge.failure_type(item, _answer("g01", "x"), "정답", sources) is None
    assert judge.failure_type(unknown, _answer("g02", "토스"), "오답", sources) == "지어냄"
    seen = _answer("g01", "틀린 답", ["wiki/docs/a.md:1-3@abc"])  # 봇이 LLM에 넘긴 조각
    assert judge.failure_type(item, seen, "오답", sources) == "근거 찾고 틀림"
    cited = _answer("g01", "틀린 답 (docs/a.md:3)")  # 베이스라인은 인용
    assert judge.failure_type(item, cited, "부분", sources) == "근거 찾고 틀림"
    missed = _answer("g01", "틀린 답", ["wiki/docs/b.md:1@abc"])
    assert judge.failure_type(item, missed, "오답", sources) == "근거 못 찾음"


class FakeLLM:
    model = "fake/judge"

    def __init__(self, replies):
        self.replies = replies

    def complete_json(self, system, user, schema, name):
        reply = self.replies[user.split("[질문]\n")[1].split("\n")[0]]
        if isinstance(reply, Exception):
            raise reply
        return LLMResult(reply, self.model, 0.001, 10, 10, 1)


def test_score_run_records_verdicts_and_item_problems(setup):
    golden, sources = setup
    answers = {
        "g01": _answer("g01", "모델은 bge다"),
        "g02": _answer("g02", "모름"),
    }
    llm = FakeLLM(
        {
            "임베딩 모델은?": {"verdict": "오답", "item_problem": False, "reason": "틀림"},
            "질문?": {"verdict": "정답", "item_problem": True, "reason": "질문이 모호"},
        }
    )

    scored, skipped = judge.score_run(golden.items, answers, sources, llm)

    assert skipped == []
    assert scored == [
        {"id": "g01", "verdict": "오답", "failure": "근거 못 찾음", "reason": "틀림"},
        {"id": "g02", "verdict": "정답", "item_problem": True, "reason": "질문이 모호"},
    ]


def test_score_run_skips_errors_and_stops_on_fatal(setup):
    golden, sources = setup
    answers = {
        "g01": _answer("g01", "e5"),
        "g02": Answer("g02", "", "시간 초과", {}),  # 답이 없는 문항은 채점하지 않는다
    }

    scored, skipped = judge.score_run(
        golden.items, answers, sources, FakeLLM({"임베딩 모델은?": LLMError("빈 답")})
    )
    assert (scored, skipped) == ([], ["g01: 빈 답"])

    with pytest.raises(LLMError, match="402"):
        judge.score_run(
            golden.items,
            answers,
            sources,
            FakeLLM({"임베딩 모델은?": LLMError("402", fatal=True)}),
        )


def test_agreement_lists_differences():
    human = {"g01": Score("정답", None, None), "g02": Score("부분", None, None)}
    llm = {
        "g01": Score("정답", None, None),
        "g02": Score("오답", None, "이유"),
        "g03": Score("정답", None, None),
    }

    same, total, differ = judge.agreement(human, llm)

    assert (same, total) == (1, 2)
    assert differ == ["g02: 사람 부분 · LLM 오답 — 이유"]


def test_score_command_does_not_overwrite_human_scores(tmp_path, capsys):
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "scores.yaml").write_text("items: []\n", encoding="utf-8")

    assert judge.main(["score", str(run_dir)]) == 1
    assert "덮어쓰지 않는다" in capsys.readouterr().err
    assert load_scores(run_dir) == {}
