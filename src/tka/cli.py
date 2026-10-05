"""사용자 입구: uv run tka <명령>. 지식 서비스(tka.service)를 부르는 얇은 껍데기다.

    tka ask "질문"        문서를 근거로 답한다 (LLM, 문장마다 인용)
    tka search "질문"     문서 조각과 인용 (MCP search_docs와 같은 글)
    tka decision "주제"   결정 로그의 결정 (MCP get_decision과 같은 글)
    tka api "경로"        API 명세 ↔ 서버 코드 ↔ 호출 코드 대조 (MCP check_api와 같은 글)
    tka log               호출 로그 (MCP와 CLI)

기본은 최신 main을 따른다(MCP와 같다). --pinned면 설정의 기준 커밋(평가와 같은 문서)이다.
호출은 data/calls.jsonl에 via "cli"로 남는다. 개발·평가 명령은 python -m tka.<모듈>로 따로 있다.
"""

from __future__ import annotations

import argparse
import sys
import time
from collections.abc import Sequence

from tka.answer.llm import DEFAULT_MODEL, LLMError, OpenRouter, load_api_key
from tka.calllog import read_calls, render_calls
from tka.decisions.table import DecisionError
from tka.ingest.fetch import FetchError
from tka.service import LOG_PATH, ROOT, KnowledgeService, default_service


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "log":
        records, bad = read_calls(ROOT / LOG_PATH)
        print(render_calls(records, bad, path=LOG_PATH, limit=args.limit, only_empty=args.empty))
        return 0

    try:
        service = default_service(pinned=args.pinned, via="cli")
        if args.command == "decision":
            print(service.get_decision(args.query, args.part, args.limit, args.all))
        elif args.command == "search":
            print(service.search_docs(args.query, args.k))
        elif args.command == "api":
            print(service.check_api(args.query, args.method, args.limit))
        else:
            _ask(service, args.question, OpenRouter(args.model, load_api_key(ROOT / ".env")))
    except (FetchError, DecisionError, LLMError) as e:
        print(f"멈춤 — {e}", file=sys.stderr)
        return 1
    return 0


def _ask(service: KnowledgeService, question: str, llm: OpenRouter) -> None:
    started = time.monotonic()
    answer, note = service.ask(question, llm)
    if note:
        print(f"알림: {note}\n")
    print(answer.render())
    cost = f"${answer.llm.cost_usd:.4f}" if answer.llm.cost_usd is not None else "비용 모름"
    commit = (service.commit or "?")[:7]
    elapsed = time.monotonic() - started
    print(f"\n({answer.llm.model} · 기준 커밋 {commit} · {elapsed:.1f}초 · {cost})")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tka", description="북적북적 팀 지식 에이전트")
    commands = parser.add_subparsers(dest="command", required=True)
    source = argparse.ArgumentParser(add_help=False)
    source.add_argument(
        "--pinned", action="store_true", help="최신 main 대신 설정의 기준 커밋 문서를 쓴다"
    )

    ask = commands.add_parser("ask", parents=[source], help="문서를 근거로 답한다 (LLM)")
    ask.add_argument("question")
    ask.add_argument(
        "--model", default=DEFAULT_MODEL, help=f"OpenRouter 모델. 기본 {DEFAULT_MODEL}"
    )

    search = commands.add_parser("search", parents=[source], help="문서 조각과 인용")
    search.add_argument("query")
    search.add_argument("-k", type=int, default=5, help="돌려줄 조각 수")

    decision = commands.add_parser("decision", parents=[source], help="결정 로그의 결정")
    decision.add_argument("query", nargs="?", default="", help="비우면 최근 결정부터")
    decision.add_argument("--part", default="", help="AI, BE, FE, CLD, TEAM, FS")
    decision.add_argument("--limit", type=int, default=10)
    decision.add_argument("--all", action="store_true", help="대체된 옛 결정도 보여 준다")

    api = commands.add_parser("api", parents=[source], help="API 명세 ↔ 코드 ↔ 호출 대조")
    api.add_argument("query", nargs="?", default="", help="경로나 그 일부. 비우면 어긋난 곳 목록")
    api.add_argument("--method", default="", help="GET, POST, PUT, PATCH, DELETE")
    api.add_argument("--limit", type=int, default=20)

    log = commands.add_parser("log", help="호출 로그 (MCP와 CLI)")
    log.add_argument("-n", "--limit", type=int, default=20, help="보여 줄 호출 수")
    log.add_argument("--empty", action="store_true", help="결과 없음·모름만")
    return parser


if __name__ == "__main__":
    sys.exit(main())
