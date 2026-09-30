"""MCP 서버 (stdio). core 함수를 도구로 감싸는 얇은 껍데기다 (plan.md D14·D17).

    uv run --directory <이 레포> python -m tka.mcp_server            최신 main을 따라간다
    uv run --directory <이 레포> python -m tka.mcp_server --pinned   설정의 기준 커밋 그대로

- 실제로 쓸 때는 최신 main을 따른다. 결정 로그는 하루에도 여러 줄 늘어난다(09-28에 3줄).
  부를 때마다 10분이 지났으면 `git ls-remote`로 main을 확인하고, 바뀌었으면 받아
  결정 표를 다시 만든다. 평가용 캐시(.cache/sources)와 섞이지 않게 .cache/live/에 받는다.
- 확인에 실패하면(네트워크 등) 멈추지 않고 마지막으로 받은 커밋 기준으로 답하며 그 사실을 알린다.
- 결정 표 검사에 문제가 있으면(보정 파일 줄 번호가 새 main과 어긋남 등) 답에 알린다.
- 검색 색인(search_docs)은 결정 표와 같은 커밋 사본으로, 처음 검색할 때 만든다. 임베딩은
  data/index.sqlite에 캐시돼 main이 바뀌어도 바뀐 청크만 새로 계산한다. get_decision만 쓰는
  세션은 e5 모델을 불러오지 않는다.
- 호출마다 data/mcp_calls.jsonl에 한 줄 남긴다(언제, 무엇을 물었나, 무엇을 돌려줬나).
  틀린 답은 골든셋 재료다.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Annotated

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations
from pydantic import Field

from tka import core
from tka.config import Config, Source, load_config
from tka.decisions.table import DecisionTable, build_table
from tka.index.vector import Embedder
from tka.ingest.fetch import FetchError, fetch_source, github_url
from tka.retrieve.search import DEFAULT_SETTINGS, SearchIndex, SearchSettings, build_index

ROOT = Path(os.environ.get("TKA_ROOT") or Path(__file__).resolve().parents[2])
CONFIG_PATH = Path("config/ktb13.yaml")
LIVE_CACHE = Path(".cache/live")
LIVE_BRANCH = "main"
REFRESH_SECONDS = 600
LS_REMOTE_TIMEOUT = 20

INSTRUCTIONS = (
    "북적북적(KTB4-13th) 팀의 결정과 문서 근거를 돌려준다. 다른 파트(AI·BE·FE·CLOUD)의 API 방식, "
    "응답 형식, 필드 이름, 설계 선택을 코드에 쓰기 전에 추측하지 말고 먼저 확인한다. 답 문장은 "
    "돌려준 근거를 읽고 직접 쓴다."
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


class KnowledgeService:
    """결정 표와 검색 색인을 들고 있다가, live면 최신 main을 따라 새로 만든다."""

    def __init__(
        self,
        config: Config,
        root: Path,
        *,
        live: bool = True,
        clock: Callable[[], float] = time.monotonic,
        url: str | None = None,
        embedder: Embedder | None = None,
        settings: SearchSettings = DEFAULT_SETTINGS,
    ) -> None:
        if config.decisions is None:
            raise ValueError("설정에 decisions가 없다")
        self.config = config
        self.root = root
        self.live = live
        self.clock = clock
        self.source = next(s for s in config.sources if s.name == config.decisions.source)
        self.url = url or github_url(self.source)
        self.embedder = embedder
        self.settings = settings
        # 지금 읽고 있는 사본: pinned면 설정 그대로, live면 .cache/live/와 받은 main 커밋
        self._config: Config = config
        self._source: Source = self.source
        self._table: DecisionTable | None = None
        self._index: SearchIndex | None = None
        self._checked_at: float | None = None
        self._note: str | None = None

    def table(self) -> tuple[DecisionTable, str | None]:
        """결정 표와, 최신 여부에 대한 알림(없으면 None)."""
        self._ensure_fresh()
        assert self._table is not None
        return self._table, self._notes()

    def search_index(self) -> tuple[SearchIndex, DecisionTable, str | None]:
        """검색 색인(같은 커밋의 결정 표와 함께). 처음 부를 때 만든다."""
        self._ensure_fresh()
        assert self._table is not None
        if self._index is None:
            self._index = build_index(
                self._config, self.root, self._source, self.embedder, self.settings, self._table
            )
        return self._index, self._table, self._notes()

    def _ensure_fresh(self) -> None:
        if not self.live:
            if self._table is None:
                self._table = build_table(self.config, self.root)
        elif self._checked_at is None or self.clock() - self._checked_at >= REFRESH_SECONDS:
            self._refresh()

    def _notes(self) -> str | None:
        assert self._table is not None
        notes = [self._note] if self._note else []
        if self._table.problems:
            notes.append(
                f"결정 표 검사 문제 {len(self._table.problems)}건. 보정 정보(이전 결정·옛 서술 "
                f"위치)가 이 커밋과 안 맞을 수 있다: {self._table.problems[0]}"
            )
        return " / ".join(notes) or None

    def _refresh(self) -> None:
        self._checked_at = self.clock()
        try:
            head = remote_head(self.url, LIVE_BRANCH)
            if self._table is None or self._table.commit != head:
                live_config = replace(self.config, cache_dir=LIVE_CACHE)
                live_source = replace(self.source, ref=head)
                fetch_source(live_config, self.root, live_source, url=self.url)
                self._table = build_table(live_config, self.root, live_source)
                self._config, self._source = live_config, live_source
                self._index = None  # 새 커밋이면 검색 색인도 다시 만든다
            self._note = None
        except FetchError as e:
            if self._table is None:
                raise
            self._note = (
                f"최신 {LIVE_BRANCH} 확인에 실패해 마지막으로 받은 "
                f"{self._table.commit[:7]} 기준으로 답한다 ({e})"
            )


def remote_head(url: str, branch: str) -> str:
    try:
        completed = subprocess.run(
            ["git", "ls-remote", url, f"refs/heads/{branch}"],
            capture_output=True,
            text=True,
            timeout=LS_REMOTE_TIMEOUT,
        )
    except subprocess.TimeoutExpired as e:
        raise FetchError(f"git ls-remote가 {LS_REMOTE_TIMEOUT}초 안에 끝나지 않았다") from e
    if completed.returncode != 0:
        raise FetchError(f"git ls-remote 실패: {completed.stderr.strip()}")
    line = completed.stdout.strip().split("\n")[0]
    if not line:
        raise FetchError(f"원격에 {branch} 브랜치가 없다")
    return line.split("\t")[0]


def build_server(service: KnowledgeService, log_path: Path | None = None) -> MCPServer:
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
        started = time.monotonic()
        table, note = service.table()
        text, found = core.get_decision(
            table,
            query,
            part,
            limit,
            aliases=service.config.aliases,
            include_superseded=include_superseded,
            note=note,
        )
        _log_call(
            log_path,
            tool="get_decision",
            args={
                "query": query,
                "part": part,
                "limit": limit,
                "include_superseded": include_superseded,
            },
            commit=table.commit,
            returned=[f"{table.log_path}:{d.line}" for d in found],
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        return text

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
        started = time.monotonic()
        index, table, note = service.search_index()
        text, hits = core.search_docs(index, table, query, k, settings=service.settings, note=note)
        _log_call(
            log_path,
            tool="search_docs",
            args={"query": query, "k": k},
            commit=table.commit,
            returned=[f"{h.chunk.path}:{h.chunk.start_line}-{h.chunk.end_line}" for h in hits],
            elapsed_ms=round((time.monotonic() - started) * 1000),
        )
        return text

    return server


def _log_call(log_path: Path | None, **record) -> None:
    if log_path is None:
        return
    record = {"at": datetime.now().astimezone().isoformat(timespec="seconds"), **record}
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError as e:
        # 로그를 못 남겨도 도구 답은 돌려준다. 원인은 stderr로 알린다(stdout은 MCP 통신용).
        print(f"호출 로그를 남기지 못했다: {e}", file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tka.mcp_server", description="MCP 서버")
    parser.add_argument("--pinned", action="store_true", help="설정의 기준 커밋 그대로 쓴다")
    args = parser.parse_args(argv)

    config = load_config(ROOT / CONFIG_PATH)
    embedder = Embedder(ROOT / config.index_path)  # e5는 처음 검색할 때 불러온다
    service = KnowledgeService(config, ROOT, live=not args.pinned, embedder=embedder)
    build_server(service, ROOT / "data" / "mcp_calls.jsonl").run("stdio")
    return 0


if __name__ == "__main__":
    sys.exit(main())
