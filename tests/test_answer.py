from types import SimpleNamespace

import pytest
from helpers import fake_embedder

from tka.answer import llm as llm_module
from tka.answer.llm import LLMError, LLMResult, OpenRouter, load_api_key
from tka.answer.pipeline import (
    SCHEMA,
    Answer,
    Sentence,
    answer_question,
    build_prompt,
    gather_sources,
    verify,
)
from tka.decisions.table import Decision, DecisionTable
from tka.ingest.chunk import Chunk
from tka.retrieve.search import SearchIndex, decision_chunks


def _decision(line, date, text, **kw) -> Decision:
    base = dict(
        part="AI",
        affected=("AI",),
        detail_path="docs/ai/4.md",
        detail_title=None,
        detail_note=None,
    )
    base.update(kw)
    return Decision(date=date, text=text, line=line, **base)


TABLE = DecisionTable(
    repo="wiki",
    commit="4f6a6a6b33",
    log_path="docs/dec/log.md",
    decisions=(
        _decision(
            20,
            "2026-09-24",
            "① 키워드 검색은 pg_trgm만 쓰고 BM25는 쓰지 않는다",
            replaces="① BM25",
            old_text_at=("docs/ai/2.md:117",),
        ),
        _decision(23, "2026-09-21", "③⑤ LangChain 도입"),
    ),
    problems=(),
)


def _doc(path, start, end, text, heading=(), callout=None) -> Chunk:
    return Chunk(
        "wiki", "4f6a6a6b33", path, "문서", tuple(heading), start, end, text, callout, False, False
    )


DOCS = [
    _doc(
        "docs/ai/2.md",
        110,
        120,
        "검색 recall 표 BM25 0.90",
        heading=("측정",),
        callout="BM25 서술은 이력이다",
    ),
    _doc("docs/ai/4.md", 1, 5, "pg_trgm 글자 조각 검색", heading=("검색",)),
]


def _index() -> SearchIndex:
    embedder, _ = fake_embedder()
    return SearchIndex(DOCS + decision_chunks(TABLE), embedder)


class FakeLLM:
    def __init__(self, data):
        self.data = data
        self.prompts = []

    def complete_json(self, system, user, schema, name):
        self.prompts.append((system, user, schema, name))
        return LLMResult(self.data, "fake/model", 0.001, 100, 20, 5)


# ── 근거 모으기·프롬프트 ──────────────────────────────────────────


def test_sources_put_decisions_first_with_code_made_citations():
    sources = gather_sources(_index(), TABLE, "검색에 BM25 쓰나", k=4)

    decisions = [s for s in sources if s.kind == "decision"]
    docs = [s for s in sources if s.kind == "doc"]
    assert decisions[0].id == "D1"
    assert decisions[0].citation == "wiki/docs/dec/log.md:20@4f6a6a6"
    assert "이전 결정: ① BM25" in decisions[0].text
    assert len({s.citation for s in decisions}) == len(decisions)  # 표 찾기·검색 결과 중복 없음
    measure = next(s for s in docs if "측정" in s.title)
    assert measure.citation == "wiki/docs/ai/2.md:110-120@4f6a6a6"
    assert "[문서 안내] BM25 서술은 이력이다" in measure.text
    assert "[주의] 117행은 옛 서술이다. 지금 결정: 2026-09-24" in measure.text


def test_prompt_lists_question_and_sources():
    sources = gather_sources(_index(), TABLE, "BM25", k=2)
    prompt = build_prompt("BM25 쓰나?", sources)

    assert prompt.startswith("[질문]\nBM25 쓰나?\n\n[근거]")
    assert all(f"[{s.id}] " in prompt for s in sources)
    assert "wiki/docs" not in prompt  # 인용 문자열은 LLM에 주지 않는다
    assert build_prompt("질문", []).endswith("(근거 없음)")


# ── 인용 검증·렌더링 ──────────────────────────────────────────────


def test_verify_keeps_only_given_source_ids():
    unknown, kept, dropped = verify(
        {
            "unknown": False,
            "sentences": [
                {"text": "pg_trgm만 쓴다.", "sources": ["D1", "C9"]},  # C9는 넘긴 적 없다
                {"text": "지어낸 문장.", "sources": ["X1"]},
                {"text": "근거 없는 문장.", "sources": []},
                {"text": "  ", "sources": ["D1"]},
            ],
        },
        {"D1", "C1"},
    )

    assert unknown is False
    assert kept == [Sentence("pg_trgm만 쓴다.", ("D1",))]
    assert [s.text for s in dropped] == ["지어낸 문장.", "근거 없는 문장."]


@pytest.mark.parametrize(
    "data", [{"sentences": []}, {"unknown": "no", "sentences": []}, {"unknown": True}]
)
def test_verify_rejects_wrong_shape(data):
    with pytest.raises(LLMError, match="스키마와 다르다"):
        verify(data, set())


def _answer(unknown, sentences, sources) -> Answer:
    return Answer(
        "질문", unknown, tuple(sentences), (), tuple(sources), LLMResult({}, "m", None, 0, 0, 0)
    )


def test_render_numbers_sources_by_first_use():
    sources = gather_sources(_index(), TABLE, "BM25", k=2)
    d1, c1 = sources[0], next(s for s in sources if s.kind == "doc")
    text = _answer(
        False,
        [Sentence("첫 문장.", (c1.id,)), Sentence("둘째 문장.", (d1.id, c1.id))],
        sources,
    ).render()

    assert text.startswith("첫 문장. [1]\n둘째 문장. [2][1]\n\n근거:\n")
    assert f"[1] {c1.citation} — {c1.title}" in text
    assert f"[2] {d1.citation} — {d1.title}" in text


def test_render_unknown():
    assert _answer(True, [], []).render() == "모름 — 근거 문서에서 질문에 대한 답을 찾지 못했다."
    sources = gather_sources(_index(), TABLE, "BM25", k=1)
    text = _answer(True, [Sentence("관련 사실.", (sources[0].id,))], sources).render()
    assert text.startswith(
        "모름 — 근거 문서에서 질문에 대한 답을 찾지 못했다.\n관련해 문서에 있는 것:\n관련 사실. [1]"
    )


def test_answer_question_end_to_end_with_fake_llm():
    llm = FakeLLM({"unknown": False, "sentences": [{"text": "안 쓴다.", "sources": ["D1", "Z9"]}]})

    answer = answer_question(_index(), TABLE, "검색에 BM25 쓰나", llm, k=3)

    system, user, schema, name = llm.prompts[0]
    assert schema is SCHEMA and name == "answer"
    assert "그 결정을 지금 값으로 답한다" in system
    assert "[질문]\n검색에 BM25 쓰나" in user
    assert answer.sentences == (Sentence("안 쓴다.", ("D1",)),)
    assert answer.render().startswith("안 쓴다. [1]")


# ── 키·OpenRouter ─────────────────────────────────────────────────


def test_api_key_from_env_var_then_last_env_file_line(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text(
        "OPENROUTER_API_KEY=first\nOTHER=x\nOPENROUTER_API_KEY='second'\n", encoding="utf-8"
    )
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert load_api_key(env) == "second"

    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    assert load_api_key(env) == "from-env"


def test_missing_api_key(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    with pytest.raises(LLMError, match="OPENROUTER_API_KEY가 없다"):
        load_api_key(tmp_path / ".env")


def _router(response=None, error=None) -> tuple[OpenRouter, dict]:
    router = OpenRouter("some/model", "key")
    sent = {}

    def create(**kwargs):
        sent.update(kwargs)
        if error:
            raise error
        return response

    router._client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=create))
    )
    return router, sent


def _response(content, cost=0.002):
    usage = SimpleNamespace(prompt_tokens=100, completion_tokens=20, model_extra={"cost": cost})
    message = SimpleNamespace(content=content)
    return SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=usage,
        model="some/model",
    )


def test_openrouter_sends_strict_schema_and_reads_cost():
    router, sent = _router(_response('{"unknown": false, "sentences": []}'))

    result = router.complete_json("sys", "user", SCHEMA, "answer")

    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["extra_body"]["provider"] == {"require_parameters": True}
    assert (result.data, result.cost_usd, result.input_tokens) == (
        {"unknown": False, "sentences": []},
        0.002,
        100,
    )


@pytest.mark.parametrize(
    ("content", "message"),
    [("", "빈 답"), ("not json", "JSON이 아닌 답"), ("[1, 2]", "JSON 객체가 아니다")],
)
def test_openrouter_bad_content(content, message):
    router, _ = _router(_response(content))

    with pytest.raises(LLMError, match=message):
        router.complete_json("s", "u", SCHEMA, "answer")


def test_openrouter_api_error_is_wrapped():
    from openai import APIConnectionError

    error = APIConnectionError(request=SimpleNamespace(method="POST", url="u"))
    router, _ = _router(error=error)

    with pytest.raises(LLMError, match="some/model 호출 실패: APIConnectionError"):
        router.complete_json("s", "u", SCHEMA, "answer")


def test_retries_are_bounded():
    assert llm_module.MAX_RETRIES == 2


def test_not_found_is_fatal_but_connection_error_is_not():
    import httpx
    from openai import NotFoundError

    request = httpx.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    not_found = NotFoundError(
        "No endpoints found", response=httpx.Response(404, request=request), body=None
    )
    router, _ = _router(error=not_found)
    with pytest.raises(LLMError) as info:
        router.complete_json("s", "u", SCHEMA, "answer")
    assert info.value.fatal is True

    from openai import APIConnectionError

    router, _ = _router(error=APIConnectionError(request=request))
    with pytest.raises(LLMError) as info:
        router.complete_json("s", "u", SCHEMA, "answer")
    assert info.value.fatal is False


def test_eval_stops_on_fatal_error(tmp_path, monkeypatch):
    from argparse import Namespace
    from pathlib import Path

    import yaml

    from tka.answer import __main__ as cli

    calls = []

    def failing(*args, **kwargs):
        calls.append(1)
        raise LLMError("some/model 호출 실패: NotFoundError: 404", fatal=True)

    monkeypatch.setattr(cli, "answer_question", failing)
    golden = Path(__file__).parent.parent / "eval" / "golden.yaml"
    args = Namespace(
        golden=golden, ids=None, out=tmp_path / "run", root=tmp_path, model="some/model"
    )

    code = cli._eval(args, SimpleNamespace(aliases=()), None, TABLE, None)

    assert code == 1
    assert len(calls) == 1  # 첫 문항에서 멈춘다
    saved = yaml.safe_load((tmp_path / "run" / "answers.yaml").read_text(encoding="utf-8"))
    assert saved["answers"][0]["error"].startswith("some/model 호출 실패")
