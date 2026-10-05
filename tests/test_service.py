import json
from dataclasses import replace

import pytest
from helpers import (
    contracts_config,
    decision_config,
    fake_embedder,
    git,
    make_contract_repos,
    make_decision_repo,
)

from tka import service as service_module
from tka.answer.llm import LLMResult
from tka.calllog import CallLog
from tka.ingest.fetch import FetchError
from tka.service import REFRESH_SECONDS, KnowledgeService


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class FakeLLM:
    model = "fake/model"

    def __init__(self, data):
        self.data = data

    def complete_json(self, system, user, schema, name):
        return LLMResult(self.data, self.model, 0.001, 100, 20, 5)


@pytest.fixture
def origin(tmp_path):
    repo = tmp_path / "origin"
    commit = make_decision_repo(repo)
    return repo, commit


def _live(tmp_path, origin, clock=None, embedder=None, log=None):
    repo, commit = origin
    config = decision_config(tmp_path, commit)
    return KnowledgeService(
        config,
        tmp_path,
        live=True,
        clock=clock or FakeClock(),
        url=str(repo),
        embedder=embedder,
        log=log or CallLog(None),
    )


def _records(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# ── 최신 main 따라가기 ────────────────────────────────────────────


def test_live_service_fetches_main_into_live_cache(tmp_path, origin):
    _, commit = origin
    service = _live(tmp_path, origin)
    assert service.commit is None

    table, note = service.table()

    assert (table.commit, note, service.commit) == (commit, None, commit)
    assert (tmp_path / ".cache" / "live" / "wiki").is_dir()
    assert not (tmp_path / ".cache" / "sources").exists()  # 평가용 캐시는 건드리지 않는다


def test_live_service_follows_new_main_after_interval(tmp_path, origin):
    repo, first = origin
    clock = FakeClock()
    service = _live(tmp_path, origin, clock)
    service.table()
    log = repo / "docs" / "dec" / "log.md"
    log.write_text(log.read_text(encoding="utf-8") + "| 09-30 | AI | 새 결정 | AI | - |\n")
    git(repo, "commit", "-q", "-am", "new decision")
    second = git(repo, "rev-parse", "HEAD")

    clock.now = REFRESH_SECONDS - 1
    assert service.table()[0].commit == first  # 아직 확인하지 않는다

    clock.now = REFRESH_SECONDS
    table, _ = service.table()
    assert table.commit == second
    assert table.decisions[-1].text == "새 결정"


def test_failed_check_keeps_last_table_and_says_so(tmp_path, origin, monkeypatch):
    _, commit = origin
    clock = FakeClock()
    service = _live(tmp_path, origin, clock)
    service.table()

    def offline(url, branch):
        raise FetchError("네트워크 없음")

    monkeypatch.setattr(service_module, "remote_head", offline)
    clock.now = REFRESH_SECONDS
    table, note = service.table()

    assert table.commit == commit
    assert note.startswith(f"최신 main 확인에 실패해 마지막으로 받은 {commit[:7]} 기준으로 답한다")


def test_failed_first_check_raises(tmp_path, origin, monkeypatch):
    def offline(url, branch):
        raise FetchError("네트워크 없음")

    monkeypatch.setattr(service_module, "remote_head", offline)

    with pytest.raises(FetchError, match="네트워크 없음"):
        _live(tmp_path, origin).table()


def test_table_problems_are_noted(tmp_path, origin):
    repo, commit = origin
    config = decision_config(
        tmp_path,
        commit,
        corrections="corrections: [{date: '09-01', contains: 없음, replaces: x}]",
    )
    service = KnowledgeService(config, tmp_path, url=str(repo), clock=FakeClock())

    _, note = service.table()

    assert "결정 표 검사 문제 1건" in note and "맞는 로그 행이 없다" in note


def test_pinned_service_uses_configured_commit(tmp_path):
    repo = tmp_path / ".cache" / "sources" / "wiki"
    commit = make_decision_repo(repo)
    service = KnowledgeService(decision_config(tmp_path, commit), tmp_path, live=False)

    table, note = service.table()

    assert (table.commit, note) == (commit, None)


def test_search_index_is_built_lazily_and_only_once(tmp_path, origin):
    embedder, model = fake_embedder()
    service = _live(tmp_path, origin, embedder=embedder)

    service.table()
    assert model.calls == 0  # 결정만 찾으면 임베딩하지 않는다

    index, table, _ = service.search_index()
    again, _, _ = service.search_index()
    assert again is index
    assert {c.path for c in index.chunks} == {
        "docs/ai/spec.md",
        "docs/ai/design.md",
        "docs/dec/log.md",
    }
    assert index.chunks[0].commit == table.commit


def test_new_main_rebuilds_the_search_index(tmp_path, origin):
    repo, _ = origin
    clock = FakeClock()
    embedder, _ = fake_embedder()
    service = _live(tmp_path, origin, clock, embedder)
    first, _, _ = service.search_index()
    (repo / "docs" / "ai" / "new.md").write_text("# 새 문서\n배송비는 0원\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "new doc")

    clock.now = REFRESH_SECONDS
    second, _, _ = service.search_index()

    assert second is not first
    assert "docs/ai/new.md" in {c.path for c in second.chunks}


# ── 도구와 호출 로그 ──────────────────────────────────────────────


def test_get_decision_returns_evidence_and_logs(tmp_path, origin):
    _, commit = origin
    path = tmp_path / "data" / "calls.jsonl"
    service = _live(tmp_path, origin, log=CallLog(path, "cli"))

    text = service.get_decision("피드")

    assert "[현행] 2026-09-28 AI — ④ 피드는 GET으로 부른다" in text
    assert f"@{commit[:7]}" in text
    [record] = _records(path)
    assert (record["via"], record["tool"], record["commit"]) == ("cli", "get_decision", commit)
    assert record["args"] == {
        "query": "피드",
        "part": "",
        "limit": 10,
        "include_superseded": False,
    }
    assert record["returned"] == ["docs/dec/log.md:8"]
    assert isinstance(record["elapsed_ms"], int)


def test_search_docs_returns_evidence_and_logs(tmp_path, origin):
    _, commit = origin
    embedder, _ = fake_embedder()
    path = tmp_path / "data" / "calls.jsonl"
    service = _live(tmp_path, origin, embedder=embedder, log=CallLog(path, "mcp"))

    text = service.search_docs("설계 도입", k=2)

    assert text.startswith(f"문서 검색 결과 (기준 커밋 {commit[:7]}, 상위 2개")
    assert f"wiki/docs/ai/design.md:1-3@{commit[:7]}" in text
    # 보정 파일이 design.md 2행을 옛 서술로 적었다 → [주의]로 지금 결정을 붙인다
    assert "[주의] 2행은 옛 서술이다. 지금 결정: 2026-09-21 ③⑤ LangChain 도입" in text
    [record] = _records(path)
    assert record["tool"] == "search_docs"
    assert record["args"] == {"query": "설계 도입", "k": 2}
    assert record["returned"][0] == "docs/ai/design.md:1-3"


def test_ask_answers_with_citations_and_logs_the_answer(tmp_path, origin):
    _, commit = origin
    embedder, _ = fake_embedder()
    path = tmp_path / "data" / "calls.jsonl"
    service = _live(tmp_path, origin, embedder=embedder, log=CallLog(path, "cli"))
    llm = FakeLLM({"unknown": False, "sentences": [{"text": "GET이다.", "sources": ["D1"]}]})

    answer, note = service.ask("피드는 어떻게 부르나", llm)

    assert note is None
    assert answer.render().startswith(
        f"GET이다. [1]\n\n근거:\n[1] wiki/docs/dec/log.md:8@{commit[:7]}"
    )
    [record] = _records(path)
    assert record["tool"] == "ask"
    assert record["args"] == {"question": "피드는 어떻게 부르나", "model": "fake/model"}
    assert record["returned"] == [f"wiki/docs/dec/log.md:8@{commit[:7]}"]
    assert (record["unknown"], record["cost_usd"]) == (False, 0.001)
    assert record["answer"] == answer.render()


def test_unknown_answer_is_logged_as_unknown(tmp_path, origin):
    embedder, _ = fake_embedder()
    path = tmp_path / "calls.jsonl"
    service = _live(tmp_path, origin, embedder=embedder, log=CallLog(path, "cli"))

    service.ask("결제 PG사는?", FakeLLM({"unknown": True, "sentences": []}))

    [record] = _records(path)
    assert (record["unknown"], record["returned"]) == (True, [])


def test_unwritable_log_does_not_break_the_tool(tmp_path, origin, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    service = _live(tmp_path, origin, log=CallLog(blocker / "calls.jsonl", "mcp"))

    assert "피드는 GET" in service.get_decision("피드")
    assert "호출 로그를 남기지 못했다" in capsys.readouterr().err


def test_no_log_writes_nothing(tmp_path, origin):
    service = _live(tmp_path, origin)  # CallLog(None): 평가 실행처럼 남기지 않는다

    service.get_decision("피드")

    assert not (tmp_path / "data").exists()


# ── API 대조표 (check_api) ────────────────────────────────────────


def _contract_service(tmp_path, origin, *, live=True, clock=None, log=None, base=None):
    """결정 표 저장소(origin) + 대조표 저장소 셋. live면 base/<이름>의 main을 따른다."""
    repo, commit = origin
    base = base or tmp_path / "origins"
    commits = make_contract_repos(base)
    config = replace(decision_config(tmp_path, commit), contracts=contracts_config(commits))
    service = KnowledgeService(
        config,
        tmp_path,
        live=live,
        clock=clock or FakeClock(),
        url=str(repo),
        log=log or CallLog(None),
        contract_urls={name: str(base / name) for name in commits},
    )
    return service, commits


def test_check_api_follows_main_of_every_contract_repo(tmp_path, origin):
    service, commits = _contract_service(tmp_path, origin)

    text = service.check_api("search")
    problems = service.check_api()

    assert "[AI] POST /search — 일치" in text
    assert f"srv/app/main.py:2@{commits['srv'][:7]}" in text
    assert "[AI] GET /gone — 서버에 없는 경로를 부른다" in problems
    assert "[AI] GET /feed — 명세에만 있다 (코드에 없음)" in problems
    assert sorted(p.name for p in (tmp_path / ".cache" / "live").iterdir()) == [
        "c-wiki",
        "srv",
        "web",
    ]
    assert not (tmp_path / ".cache" / "sources").exists()  # 평가용 캐시는 건드리지 않는다


def test_check_api_rebuilds_when_any_repo_main_moves(tmp_path, origin):
    clock = FakeClock()
    service, _ = _contract_service(tmp_path, origin, clock=clock)
    assert "명세에만" in service.check_api("feed")
    srv = tmp_path / "origins" / "srv"
    (srv / "app" / "main.py").write_text(
        'app = FastAPI()\n@app.post("/search")\n@app.get("/feed")\n', encoding="utf-8"
    )
    git(srv, "commit", "-qam", "feed 추가")

    clock.now = REFRESH_SECONDS - 1
    assert "명세에만" in service.check_api("feed")  # 아직 확인하지 않는다
    clock.now = REFRESH_SECONDS
    assert "[AI] GET /feed — 일치" in service.check_api("feed")


def test_failed_contract_check_keeps_last_report_and_says_so(tmp_path, origin, monkeypatch):
    clock = FakeClock()
    service, _ = _contract_service(tmp_path, origin, clock=clock)
    service.check_api()

    def offline(url, branch):
        raise FetchError("네트워크 없음")

    monkeypatch.setattr(service_module, "remote_head", offline)
    clock.now = REFRESH_SECONDS
    text = service.check_api("search")

    assert "알림: 최신 main 확인에 실패해 마지막으로 받은 커밋 기준으로" in text
    assert "(네트워크 없음)" in text
    assert "[AI] POST /search — 일치" in text


def test_pinned_check_api_reads_configured_commits(tmp_path, origin):
    service, _ = _contract_service(
        tmp_path, origin, live=False, base=tmp_path / ".cache" / "sources"
    )

    assert "[AI] POST /search — 일치" in service.check_api("/search", method="POST")
    assert not (tmp_path / ".cache" / "live").exists()


def test_check_api_logs_commits_of_every_repo(tmp_path, origin):
    path = tmp_path / "data" / "calls.jsonl"
    service, commits = _contract_service(tmp_path, origin, log=CallLog(path, "mcp"))

    service.check_api("search", method="POST")

    [record] = _records(path)
    assert record["tool"] == "check_api"
    assert record["args"] == {"query": "search", "method": "POST", "limit": 20}
    assert record["commits"] == {name: c[:7] for name, c in commits.items()}
    assert record["returned"] == [
        "c-wiki/docs/api.md:1",
        "srv/app/main.py:2",
        "web/src/api.ts:1",
    ]


def test_check_api_without_contracts_config_stops(tmp_path, origin):
    service = _live(tmp_path, origin)

    with pytest.raises(FetchError, match="contracts가 없다"):
        service.check_api()
