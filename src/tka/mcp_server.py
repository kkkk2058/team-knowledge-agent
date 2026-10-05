"""MCP 서버 (stdio). 지식 서비스(tka.service)를 도구로 감싸는 얇은 껍데기다 (plan.md D14·D17).

    uv run --directory <이 레포> python -m tka.mcp_server            최신 main을 따라간다
    uv run --directory <이 레포> python -m tka.mcp_server --pinned   설정의 기준 커밋 그대로

- 도구는 답이 아니라 근거를 돌려준다. 답 문장은 부르는 쪽(Claude Code)이 쓴다.
- 최신 main 따라가기와 호출 로그(data/calls.jsonl, via "mcp")는 서비스가 한다.
- 도구 함수는 MCP 서버의 다른 스레드에서 불린다 (임베딩 캐시가 연결을 짧게 여는 이유).
"""

from __future__ import annotations

import argparse
import sys
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from tka.service import KnowledgeService, default_service

INSTRUCTIONS = (
    "북적북적(KTB4-13th) 팀의 결정과 문서 근거, API 명세와 실제 코드의 대조 결과를 돌려준다. "
    "다른 파트(AI·BE·FE·CLOUD)의 API 경로·메서드, 응답 형식, 필드 이름, 설계 선택을 코드에 쓰기 "
    "전에 추측하지 말고 먼저 확인한다. 답 문장은 돌려준 근거를 읽고 직접 쓴다."
)
CHECK_API_DESCRIPTION = (
    "KTB4-13th 팀 API의 명세(AI 명세·풀스택 FS-2)와 실제 서버 코드(AI FastAPI·BE Spring), "
    "그 API를 부르는 코드(FE→BE, BE→AI)를 맞춘 결과를 돌려준다. 다른 파트 API를 부르거나 "
    "구현하는 코드를 쓰기 전, PR을 점검할 때 '이 경로·메서드가 명세와 맞나, 서버에 실제로 있나, "
    "누가 부르나'를 확인하려고 부른다. 엔드포인트마다 상태(일치·메서드 다름·명세에만·코드에만·"
    "서버에 없음)와 `레포/경로:줄@커밋` 위치를 준다. 검색어를 비우면 어긋난 곳 목록이다. "
    "요청·응답 필드는 보지 않는다 — 필드는 search_docs로 명세를 찾는다."
)
GET_DECISION_DESCRIPTION = (
    "KTB4-13th 팀 결정 로그에서 기술 결정을 찾는다. 다른 파트의 API 방식·응답 형식·필드·설계 선택"
    "(프레임워크, 검색 방식, DB, 배포 등)을 코드에 쓰거나 PR을 점검하기 전에 부른다. 결정마다 상태"
    "(현행·대체됨), 뒤집힌 이전 결정, 옛 서술이 남은 곳, `레포/경로:줄@커밋` 인용을 돌려준다. "
    "결정 로그 기준이라 로그에 없는 결정은 나오지 않는다. 그때는 search_docs로 문서를 찾는다."
)
SEARCH_DOCS_DESCRIPTION = (
    "KTB4-13th 팀 문서(wiki docs/: AI API 명세·설계, 클라우드 설계, 풀스택 API·테이블 명세·"
    "정책, 결정 로그)에서 질문과 관련된 조각을 찾아 원문과 `레포/경로:줄@커밋` 인용을 돌려준다. "
    "다른 파트의 API 경로·요청/응답 필드·테이블 컬럼·정책 값·장애 동작·설계 이유를 코드에 쓰기 "
    "전에 부른다. "
    "조각에 결정으로 바뀐 옛 서술이 있으면 [주의]로 지금 결정을 알린다. 결정이 바뀌었는지는 "
    "get_decision으로 함께 확인한다."
)


def build_server(service: KnowledgeService) -> MCPServer:
    server = MCPServer("tka", instructions=INSTRUCTIONS)

    @server.tool(
        description=GET_DECISION_DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
    )
    def get_decision(
        query: Annotated[
            str, Field(description="찾을 주제. 예: 'feed 메서드', 'LangChain', '응답 형식'")
        ] = "",
        part: Annotated[
            str, Field(description="파트로 좁힌다: AI, BE, FE, CLD, TEAM, FS. 비우면 전체")
        ] = "",
        limit: Annotated[int, Field(description="돌려줄 결정 수", ge=1, le=30)] = 10,
        include_superseded: Annotated[
            bool, Field(description="대체된 옛 결정도 따로 보여 준다 (이력을 물을 때)")
        ] = False,
    ) -> str:
        return service.get_decision(query, part, limit, include_superseded)

    @server.tool(
        description=SEARCH_DOCS_DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
    )
    def search_docs(
        query: Annotated[
            str,
            Field(description="찾을 내용을 문장으로. 예: '상품 목록 API 페이지 방식', '탈퇴 복구'"),
        ],
        k: Annotated[int, Field(description="돌려줄 조각 수", ge=1, le=10)] = 5,
    ) -> str:
        return service.search_docs(query, k)

    @server.tool(
        description=CHECK_API_DESCRIPTION,
        annotations=ToolAnnotations(read_only_hint=True, idempotent_hint=True),
    )
    def check_api(
        query: Annotated[
            str,
            Field(
                description="API 경로나 그 일부, 또는 명세 설명 낱말. 예: '/api/v1/cart/items', "
                "'recommend/feed', '장바구니'. 비우면 어긋난 곳 목록"
            ),
        ] = "",
        method: Annotated[str, Field(description="GET·POST·PUT·PATCH·DELETE. 비우면 전부")] = "",
        limit: Annotated[int, Field(description="돌려줄 API 수", ge=1, le=60)] = 20,
    ) -> str:
        return service.check_api(query, method, limit)

    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tka.mcp_server", description="MCP 서버")
    parser.add_argument("--pinned", action="store_true", help="설정의 기준 커밋 그대로 쓴다")
    args = parser.parse_args(argv)

    build_server(default_service(pinned=args.pinned, via="mcp")).run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
