import json
from dataclasses import replace

import pytest
from helpers import (
    contracts_config,
    decision_config,
    fake_embedder,
    make_contract_repos,
    make_decision_repo,
)

from tka import cli
from tka.answer.llm import LLMResult
from tka.calllog import CallLog
from tka.ingest.fetch import FetchError
from tka.service import LOG_PATH, KnowledgeService


class FakeLLM:
    def __init__(self, model, key):
        self.model = model

    def complete_json(self, system, user, schema, name):
        data = {"unknown": False, "sentences": [{"text": "GET이다.", "sources": ["D1"]}]}
        return LLMResult(data, self.model, 0.0012, 100, 20, 5)


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    """기준 커밋 사본으로 도는 서비스를 tka 명령에 끼운다. 호출 로그는 tmp_path/data/calls.jsonl."""
    commit = make_decision_repo(tmp_path / ".cache" / "sources" / "wiki")
    contract_commits = make_contract_repos(tmp_path / ".cache" / "sources")
    embedder, _ = fake_embedder()
    service = KnowledgeService(
        replace(decision_config(tmp_path, commit), contracts=contracts_config(contract_commits)),
        tmp_path,
        live=False,
        embedder=embedder,
        log=CallLog(tmp_path / LOG_PATH, "cli"),
    )
    asked = []

    def default_service(**kwargs):
        asked.append(kwargs)
        return service

    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "default_service", default_service)
    return commit, asked


def test_decision_prints_evidence_and_logs_as_cli(tmp_path, pinned, capsys):
    commit, asked = pinned

    code = cli.main(["decision", "피드는", "--pinned"])

    assert code == 0
    assert asked == [{"pinned": True, "via": "cli"}]
    assert "[현행] 2026-09-28 AI — ④ 피드는 GET으로 부른다" in capsys.readouterr().out
    record = json.loads((tmp_path / LOG_PATH).read_text(encoding="utf-8"))
    assert (record["via"], record["tool"], record["commit"]) == ("cli", "get_decision", commit)


def test_search_follows_main_unless_pinned(pinned, capsys):
    _, asked = pinned

    assert cli.main(["search", "설계 도입", "-k", "1"]) == 0

    assert asked == [{"pinned": False, "via": "cli"}]
    assert "상위 1개" in capsys.readouterr().out


def test_ask_prints_answer_and_footer(pinned, monkeypatch, capsys):
    commit, _ = pinned
    monkeypatch.setattr(cli, "load_api_key", lambda env: "key")
    monkeypatch.setattr(cli, "OpenRouter", FakeLLM)

    assert cli.main(["ask", "피드는 어떻게 부르나", "--model", "fake/model"]) == 0

    out = capsys.readouterr().out
    assert out.startswith("GET이다. [1]\n\n근거:\n[1] wiki/docs/dec/log.md:8@")
    assert f"(fake/model · 기준 커밋 {commit[:7]} · " in out and "$0.0012)" in out


def test_api_prints_contract_evidence_and_logs_as_cli(tmp_path, pinned, capsys):
    assert cli.main(["api", "gone", "--method", "GET", "--pinned"]) == 0

    out = capsys.readouterr().out
    assert "[AI] GET /gone — 서버에 없는 경로를 부른다" in out
    records = [json.loads(x) for x in (tmp_path / LOG_PATH).read_text().splitlines()]
    assert [(r["via"], r["tool"], r["args"]["query"]) for r in records] == [
        ("cli", "check_api", "gone")
    ]
    assert cli.main(["log"]) == 0
    assert "3개 레포  gone" in capsys.readouterr().out  # 여러 레포를 읽은 호출


def test_log_shows_calls(tmp_path, pinned, capsys):
    cli.main(["decision", "피드"])
    capsys.readouterr()

    assert cli.main(["log"]) == 0

    out = capsys.readouterr().out
    assert out.startswith(f"호출 로그 {LOG_PATH} — 1건")
    assert "cli  get_decision  1개" in out


def test_log_without_calls(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "ROOT", tmp_path)

    assert cli.main(["log", "--empty"]) == 0

    assert capsys.readouterr().out.startswith("호출 로그가 아직 없다")


def test_fetch_failure_stops_with_reason(monkeypatch, capsys):
    def offline(**kwargs):
        raise FetchError("git ls-remote 실패: 네트워크 없음")

    monkeypatch.setattr(cli, "default_service", offline)

    assert cli.main(["decision", "피드"]) == 1
    assert "멈춤 — git ls-remote 실패" in capsys.readouterr().err


def test_ask_without_key_stops(tmp_path, pinned, monkeypatch, capsys):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

    assert cli.main(["ask", "질문"]) == 1
    assert "OPENROUTER_API_KEY가 없다" in capsys.readouterr().err


def test_command_is_required():
    with pytest.raises(SystemExit):
        cli.main([])
