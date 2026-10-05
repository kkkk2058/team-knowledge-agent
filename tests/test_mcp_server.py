import asyncio
import json
from dataclasses import replace

from helpers import (
    contracts_config,
    decision_config,
    fake_embedder,
    make_contract_repos,
    make_decision_repo,
)

from tka.calllog import CallLog
from tka.mcp_server import build_server
from tka.service import KnowledgeService


def _server(tmp_path, log=None):
    repo = tmp_path / "origin"
    commit = make_decision_repo(repo)
    embedder, _ = fake_embedder()
    service = KnowledgeService(
        decision_config(tmp_path, commit),
        tmp_path,
        url=str(repo),
        embedder=embedder,
        log=log or CallLog(None),
    )
    return build_server(service), commit


def _call(server, tool, **args) -> str:
    result = asyncio.run(server.call_tool(tool, args))
    return result.content[0].text


def test_tool_is_listed_read_only_with_when_to_call(tmp_path):
    server, _ = _server(tmp_path)

    tools = asyncio.run(server.list_tools())

    by_name = {t.name: t for t in tools}
    # 답변(ask)은 도구로 내지 않는다 (D17)
    assert set(by_name) == {"get_decision", "search_docs", "check_api"}
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
    assert "PR을 점검할 때" in by_name["check_api"].description
    assert "요청·응답 필드는 보지 않는다" in by_name["check_api"].description
    assert set(by_name["check_api"].input_schema["properties"]) == {"query", "method", "limit"}


def test_tools_call_the_service_and_log_as_mcp(tmp_path):
    path = tmp_path / "data" / "calls.jsonl"
    server, commit = _server(tmp_path, CallLog(path, "mcp"))

    decision = _call(server, "get_decision", query="피드")
    docs = _call(server, "search_docs", query="설계 도입", k=2)

    assert "[현행] 2026-09-28 AI — ④ 피드는 GET으로 부른다" in decision
    assert docs.startswith(f"문서 검색 결과 (기준 커밋 {commit[:7]}, 상위 2개")
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [(r["via"], r["tool"]) for r in records] == [
        ("mcp", "get_decision"),
        ("mcp", "search_docs"),
    ]


def test_check_api_tool_returns_contract_evidence(tmp_path):
    repo = tmp_path / "origin"
    commit = make_decision_repo(repo)
    commits = make_contract_repos(tmp_path / ".cache" / "sources")
    config = replace(decision_config(tmp_path, commit), contracts=contracts_config(commits))
    path = tmp_path / "data" / "calls.jsonl"
    server = build_server(KnowledgeService(config, tmp_path, live=False, log=CallLog(path, "mcp")))

    text = _call(server, "check_api", query="/search", method="POST")

    assert "[AI] POST /search — 일치" in text
    assert f"web/src/api.ts:1@{commits['web'][:7]}" in text
    [record] = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert (record["via"], record["tool"]) == ("mcp", "check_api")
