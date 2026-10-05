"""입구(MCP 서버, tka 명령)가 같이 쓰는 지식 서비스. 입구는 이걸 부르는 얇은 껍데기다.

- 실제로 쓸 때는 최신 main을 따른다. 결정 로그는 하루에도 여러 줄 늘어난다(09-28에 3줄).
  부를 때마다 10분이 지났으면 `git ls-remote`로 main을 확인하고, 바뀌었으면 받아
  결정 표를 다시 만든다. 평가용 캐시(.cache/sources)와 섞이지 않게 .cache/live/에 받는다.
- pinned면 설정의 기준 커밋 그대로다(평가와 같은 문서).
- 확인에 실패하면(네트워크 등) 멈추지 않고 마지막으로 받은 커밋 기준으로 답하며 그 사실을 알린다.
- 결정 표 검사에 문제가 있으면(보정 파일 줄 번호가 새 main과 어긋남 등) 답에 알린다.
- 검색 색인은 결정 표와 같은 커밋 사본으로, 처음 검색할 때 만든다. 임베딩은
  data/index.sqlite에 캐시돼 main이 바뀌어도 바뀐 청크만 새로 계산한다. 결정만 찾는
  호출은 e5 모델을 불러오지 않는다.
- 호출마다 호출 로그(tka.calllog)에 한 줄 남긴다.
- API 대조표(check_api)도 같은 방식이다. 네 레포(contracts.sources)의 main을 10분마다 확인하고,
  하나라도 바뀌면 .cache/live/에 받아 대조표를 다시 만든다. 파싱만 해서 몇 초 안에 끝난다.
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Callable, Mapping
from dataclasses import replace
from pathlib import Path

from tka import core
from tka.answer.llm import OpenRouter
from tka.answer.pipeline import Answer, answer_question
from tka.calllog import NO_LOG, CallLog
from tka.config import Config, Source, load_config
from tka.contracts.compare import Report
from tka.contracts.report import build_report
from tka.decisions.table import DecisionTable, build_table
from tka.index.vector import Embedder
from tka.ingest.fetch import FetchError, fetch_source, github_url
from tka.retrieve.search import DEFAULT_SETTINGS, SearchIndex, SearchSettings, build_index

ROOT = Path(os.environ.get("TKA_ROOT") or Path(__file__).resolve().parents[2])
CONFIG_PATH = Path("config/ktb13.yaml")
LOG_PATH = Path("data/calls.jsonl")
LIVE_CACHE = Path(".cache/live")
LIVE_BRANCH = "main"
REFRESH_SECONDS = 600
LS_REMOTE_TIMEOUT = 20


class KnowledgeService:
    """결정 표와 검색 색인을 들고 있다가, live면 최신 main을 따라 새로 만든다.

    get_decision·search_docs·ask는 부르고 호출 로그를 남긴다.
    """

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
        log: CallLog = NO_LOG,
        contract_urls: Mapping[str, str] | None = None,
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
        self.log = log
        # 지금 읽고 있는 사본: pinned면 설정 그대로, live면 .cache/live/와 받은 main 커밋
        self._config: Config = config
        self._source: Source = self.source
        self._table: DecisionTable | None = None
        self._index: SearchIndex | None = None
        self._checked_at: float | None = None
        self._note: str | None = None
        # API 대조표: 소스 이름 → 받을 주소 (테스트는 로컬 저장소), 지금 읽는 main 커밋들
        self.contract_urls = dict(contract_urls or {})
        self._report: Report | None = None
        self._report_heads: dict[str, str] = {}
        self._contracts_checked_at: float | None = None
        self._contracts_note: str | None = None

    @property
    def commit(self) -> str | None:
        """지금 읽고 있는 소스 커밋. 아직 한 번도 읽지 않았으면 None."""
        return self._table.commit if self._table else None

    # ── 도구 ──────────────────────────────────────────────────────────

    def get_decision(
        self, query: str = "", part: str = "", limit: int = 10, include_superseded: bool = False
    ) -> str:
        started = time.monotonic()
        table, note = self.table()
        text, found = core.get_decision(
            table,
            query,
            part,
            limit,
            aliases=self.config.aliases,
            include_superseded=include_superseded,
            note=note,
        )
        self.log.write(
            tool="get_decision",
            args={
                "query": query,
                "part": part,
                "limit": limit,
                "include_superseded": include_superseded,
            },
            commit=table.commit,
            returned=[f"{table.log_path}:{d.line}" for d in found],
            elapsed_ms=_ms_since(started),
        )
        return text

    def search_docs(self, query: str, k: int = 5) -> str:
        started = time.monotonic()
        index, table, note = self.search_index()
        text, hits = core.search_docs(index, table, query, k, settings=self.settings, note=note)
        self.log.write(
            tool="search_docs",
            args={"query": query, "k": k},
            commit=table.commit,
            returned=[f"{h.chunk.path}:{h.chunk.start_line}-{h.chunk.end_line}" for h in hits],
            elapsed_ms=_ms_since(started),
        )
        return text

    def check_api(self, query: str = "", method: str = "", limit: int = core.API_LIMIT) -> str:
        started = time.monotonic()
        report, note = self.contract_report()
        text, found = core.check_api(report, query, method, limit, note=note)
        self.log.write(
            tool="check_api",
            args={"query": query, "method": method, "limit": limit},
            commit=None,
            commits=report.commits,
            returned=[e.where for entry in found for e in entry.endpoints()],
            elapsed_ms=_ms_since(started),
        )
        return text

    def ask(self, question: str, llm: OpenRouter) -> tuple[Answer, str | None]:
        """문서를 근거로 답한다 (CLI용, MCP는 근거만 돌려준다 D17). (답, 최신 여부 알림)."""
        started = time.monotonic()
        index, table, note = self.search_index()
        answer = answer_question(
            index, table, question, llm, settings=self.settings, aliases=self.config.aliases
        )
        self.log.write(
            tool="ask",
            args={"question": question, "model": llm.model},
            commit=table.commit,
            returned=[s.citation for s in answer.cited()],
            elapsed_ms=_ms_since(started),
            unknown=answer.unknown,
            dropped_sentences=len(answer.dropped),
            cost_usd=answer.llm.cost_usd,
            answer=answer.render(),
        )
        return answer, note

    # ── 결정 표·색인 ──────────────────────────────────────────────────

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

    # ── API 대조표 ────────────────────────────────────────────────────

    def contract_report(self) -> tuple[Report, str | None]:
        """대조표와, 최신 여부에 대한 알림(없으면 None). 설정에 contracts가 없으면 FetchError."""
        if self.config.contracts is None:
            raise FetchError("설정에 contracts가 없다 (config/ktb13.yaml)")
        if not self.live:
            if self._report is None:
                self._report = build_report(self.config, self.root)
        elif (
            self._contracts_checked_at is None
            or self.clock() - self._contracts_checked_at >= REFRESH_SECONDS
        ):
            self._refresh_contracts()
        assert self._report is not None
        return self._report, self._contracts_note

    def _refresh_contracts(self) -> None:
        contracts = self.config.contracts
        assert contracts is not None
        self._contracts_checked_at = self.clock()
        try:
            heads = {
                s.name: remote_head(self._contract_url(s), LIVE_BRANCH) for s in contracts.sources
            }
            if self._report is None or heads != self._report_heads:
                sources = tuple(replace(s, ref=heads[s.name]) for s in contracts.sources)
                live_config = replace(
                    self.config,
                    cache_dir=LIVE_CACHE,
                    contracts=replace(contracts, sources=sources),
                )
                for s in sources:
                    fetch_source(live_config, self.root, s, url=self._contract_url(s))
                self._report = build_report(live_config, self.root)
                self._report_heads = heads
            self._contracts_note = None
        except FetchError as e:
            if self._report is None:
                raise
            self._contracts_note = (
                f"최신 {LIVE_BRANCH} 확인에 실패해 마지막으로 받은 커밋 기준으로 답한다 ({e})"
            )

    def _contract_url(self, source: Source) -> str:
        return self.contract_urls.get(source.name) or github_url(source)


def default_service(*, pinned: bool, via: str) -> KnowledgeService:
    """이 레포(ROOT)의 설정·임베딩 캐시·호출 로그를 쓰는 서비스. 입구마다 via를 적는다."""
    config = load_config(ROOT / CONFIG_PATH)
    embedder = Embedder(ROOT / config.index_path)  # e5는 처음 검색할 때 불러온다
    return KnowledgeService(
        config, ROOT, live=not pinned, embedder=embedder, log=CallLog(ROOT / LOG_PATH, via)
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


def _ms_since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
