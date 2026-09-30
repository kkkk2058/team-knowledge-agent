import asyncio
import json

import pytest
from helpers import decision_config, fake_embedder, git, make_decision_repo

from tka import mcp_server
from tka.ingest.fetch import FetchError
from tka.mcp_server import KnowledgeService, build_server


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def origin(tmp_path):
    repo = tmp_path / "origin"
    commit = make_decision_repo(repo)
    return repo, commit


def _live(tmp_path, origin, clock=None, embedder=None):
    repo, commit = origin
    config = decision_config(tmp_path, commit)
    return KnowledgeService(
        config,
        tmp_path,
        live=True,
        clock=clock or FakeClock(),
        url=str(repo),
        embedder=embedder,
    )


def _call(server, tool="get_decision", **args) -> str:
    result = asyncio.run(server.call_tool(tool, args))
    return result.content[0].text


def test_tool_is_listed_read_only_with_when_to_call(tmp_path, origin):
    server = build_server(_live(tmp_path, origin))

    tools = asyncio.run(server.list_tools())

    by_name = {t.name: t for t in tools}
    assert set(by_name) == {"get_decision", "search_docs"}
    assert "코드에 쓰거나 PR을 점검하기 전에 부른다" in by_name["get_decision"].description
    assert "코드에 쓰기 전에 부른다" in by_name["search_docs"].description
    assert all(t.annotations.read_only_hint is True for t in tools)
    assert set(by_name["get_decision"].input_schema["properties"]) == {
        "query",
        "part",
        "limit",
        "include_superseded",
    }
    assert set(by_name["search_docs"].input_schema["properties"]) == {"query", "k"}


def test_live_service_fetches_main_into_live_cache(tmp_path, origin):
    _, commit = origin
    service = _live(tmp_path, origin)

    table, note = service.table()

    assert (table.commit, note) == (commit, None)
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

    clock.now = mcp_server.REFRESH_SECONDS - 1
    assert service.table()[0].commit == first  # 아직 확인하지 않는다

    clock.now = mcp_server.REFRESH_SECONDS
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

    monkeypatch.setattr(mcp_server, "remote_head", offline)
    clock.now = mcp_server.REFRESH_SECONDS
    table, note = service.table()

    assert table.commit == commit
    assert note.startswith(f"최신 main 확인에 실패해 마지막으로 받은 {commit[:7]} 기준으로 답한다")


def test_failed_first_check_raises(tmp_path, origin, monkeypatch):
    def offline(url, branch):
        raise FetchError("네트워크 없음")

    monkeypatch.setattr(mcp_server, "remote_head", offline)

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


def test_call_returns_evidence_and_logs_the_call(tmp_path, origin):
    _, commit = origin
    log = tmp_path / "data" / "calls.jsonl"
    server = build_server(_live(tmp_path, origin), log)

    text = _call(server, query="피드")

    assert "[현행] 2026-09-28 AI — ④ 피드는 GET으로 부른다" in text
    assert f"@{commit[:7]}" in text
    record = json.loads(log.read_text(encoding="utf-8").strip())
    assert record["tool"] == "get_decision"
    assert record["args"] == {
        "query": "피드",
        "part": "",
        "limit": 10,
        "include_superseded": False,
    }
    assert record["commit"] == commit
    assert record["returned"] == ["docs/dec/log.md:8"]


def test_unwritable_log_does_not_break_the_tool(tmp_path, origin, capsys):
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    server = build_server(_live(tmp_path, origin), blocker / "calls.jsonl")

    assert "피드는 GET" in _call(server, query="피드")
    assert "호출 로그를 남기지 못했다" in capsys.readouterr().err


def test_pinned_service_uses_configured_commit(tmp_path):
    repo = tmp_path / ".cache" / "sources" / "wiki"
    commit = make_decision_repo(repo)
    service = KnowledgeService(decision_config(tmp_path, commit), tmp_path, live=False)

    table, note = service.table()

    assert (table.commit, note) == (commit, None)


# ── search_docs ───────────────────────────────────────────────────


def test_search_index_is_built_lazily_and_only_once(tmp_path, origin):
    embedder, model = fake_embedder()
    service = _live(tmp_path, origin, embedder=embedder)

    service.table()
    assert model.calls == 0  # get_decision만 쓰면 임베딩하지 않는다

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

    clock.now = mcp_server.REFRESH_SECONDS
    second, _, _ = service.search_index()

    assert second is not first
    assert "docs/ai/new.md" in {c.path for c in second.chunks}


def test_search_docs_returns_evidence_and_logs(tmp_path, origin):
    _, commit = origin
    embedder, _ = fake_embedder()
    log = tmp_path / "data" / "calls.jsonl"
    server = build_server(_live(tmp_path, origin, embedder=embedder), log)

    text = _call(server, "search_docs", query="설계 도입", k=2)

    assert text.startswith(f"문서 검색 결과 (기준 커밋 {commit[:7]}, 상위 2개")
    assert f"wiki/docs/ai/design.md:1-3@{commit[:7]}" in text
    # 보정 파일이 design.md 2행을 옛 서술로 적었다 → [주의]로 지금 결정을 붙인다
    assert "[주의] 2행은 옛 서술이다. 지금 결정: 2026-09-21 ③⑤ LangChain 도입" in text
    record = json.loads(log.read_text(encoding="utf-8").strip())
    assert record["tool"] == "search_docs"
    assert record["args"] == {"query": "설계 도입", "k": 2}
    assert record["returned"][0] == "docs/ai/design.md:1-3"
