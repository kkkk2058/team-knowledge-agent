from helpers import fake_embedder

from tka import core
from tka.core import find_decisions, get_decision, render_decisions, search_docs
from tka.decisions.table import Decision, DecisionTable
from tka.ingest.chunk import Chunk
from tka.retrieve.search import SearchIndex

ALIASES = (("④", "feed", "피드"), ("①", "search", "검색"))


def _d(line, date, part, text, **kw) -> Decision:
    defaults = dict(affected=("AI",), detail_path="docs/a.md", detail_title=None, detail_note=None)
    defaults.update(kw)
    return Decision(date=date, part=part, text=text, line=line, **defaults)


TABLE = DecisionTable(
    repo="KTB4-13th-wiki",
    commit="4f6a6a6b33a03c69",
    log_path="docs/dec/000-decision-log.md",
    decisions=(
        _d(13, "2026-09-28", "AI", "④ 피드는 이력으로 개인화", affected=("AI", "BE")),
        _d(14, "2026-09-28", "AI", "③ SSE는 V2"),
        _d(
            20,
            "2026-09-24",
            "AI",
            "① 키워드 검색은 pg_trgm만 쓰고 BM25는 쓰지 않는다",
            replaces="① BM25",
            old_text_at=("docs/ai/2.md:117",),
        ),
        _d(23, "2026-09-21", "AI", "③⑤ LangChain 도입", status="대체됨", superseded_by=13),
        _d(
            27,
            "2026-09-17",
            "FS·AI",
            "공통 응답 `{message, data}`",
            affected=("FS", "AI"),
            detail_path=None,
            detail_note="형식 변경 시 이 로그에 추가",
        ),
        _d(29, "2026-09-10", "CLD", "배포는 Recreate", affected=()),
    ),
    problems=(),
)


def _lines(**kw) -> list[int]:
    return [d.line for d in find_decisions(TABLE, aliases=ALIASES, **kw)]


def test_empty_query_lists_newest_first_keeping_log_order_within_a_day():
    assert _lines(include_superseded=True) == [13, 14, 20, 23, 27, 29]


def test_superseded_decisions_are_hidden_by_default():
    assert 23 not in _lines()  # 23행은 13행이 대체했다
    assert _lines(query="LangChain") == []
    assert _lines(query="LangChain", include_superseded=True) == [23]


def test_alias_and_particle_find_the_same_decision():
    assert _lines(query="feed") == [13]
    assert _lines(query="피드는 어떻게 불러?") == [13]
    assert _lines(query="LangChain을 쓰나", include_superseded=True) == [23]


def test_more_matching_concepts_rank_first():
    # "검색"(①)과 "BM25" 둘 다 맞는 행이 앞선다
    assert _lines(query="검색 BM25 피드")[0] == 20


def test_part_filter_uses_part_and_affected():
    assert _lines(part="CLD") == [29]
    assert _lines(part="fs") == [27]  # "FS·AI"를 나눠 본다
    assert _lines(part="BE") == [13]  # 영향 파트도 본다


def test_limit():
    assert _lines(limit=2) == [13, 14]


def test_render_shows_status_history_and_citation():
    text = render_decisions(TABLE, [TABLE.decisions[2], TABLE.decisions[3]], query="검색")

    assert text.startswith(
        "결정 로그 기준 (KTB4-13th-wiki@4f6a6a6, 전체 6개, 검색어 '검색'). "
        "로그에 없는 결정은 여기 나오지 않는다."
    )
    assert "[현행] 2026-09-24 AI — ① 키워드 검색은" in text
    assert "  이전 결정: ① BM25" in text
    assert "  옛 서술이 아직 남은 곳: docs/ai/2.md:117" in text
    assert "  근거: KTB4-13th-wiki/docs/dec/000-decision-log.md:20@4f6a6a6" in text
    assert "[대체됨] 2026-09-21 AI — ③⑤ LangChain 도입" in text
    assert "  대체한 결정: 2026-09-28 ④ 피드는 이력으로 개인화" in text


def test_current_decision_shows_what_it_replaced():
    text = render_decisions(TABLE, [TABLE.decisions[0]])

    assert "  대체한 이전 결정: 2026-09-21 ③⑤ LangChain 도입 (로그 23행)" in text


def test_render_no_match_says_the_log_may_not_have_it():
    text, found = get_decision(TABLE, "로켓 발사", aliases=ALIASES, note="확인 실패")

    assert found == []
    assert "알림: 확인 실패" in text
    assert "결정 로그에 없는 결정일 수 있으니 관련 문서를 직접 확인한다" in text


def test_detail_note_is_shown_when_there_is_no_link():
    text, _ = get_decision(TABLE, "공통 응답", aliases=ALIASES)

    assert "상세: 형식 변경 시 이 로그에 추가" in text


# ── search_docs ───────────────────────────────────────────────────


def _chunk(path, start, end, text, heading=(), callout=None) -> Chunk:
    return Chunk(
        "KTB4-13th-wiki",
        "4f6a6a6b33",
        path,
        "문서",
        tuple(heading),
        start,
        end,
        text,
        callout,
        False,
        False,
    )


def _search_index(chunks) -> SearchIndex:
    embedder, _ = fake_embedder()
    return SearchIndex(chunks, embedder)


def test_search_docs_renders_citation_title_callout_and_caution():
    chunks = [
        _chunk(
            "docs/ai/2.md",
            110,
            120,
            "검색 recall 표 BM25 0.90",
            heading=("측정",),
            callout="이 문서의 BM25 서술은 이력이다",
        ),
        _chunk("docs/ai/4.md", 1, 5, "파이프라인 단계", heading=("단계",)),
    ]

    text, hits = search_docs(_search_index(chunks), TABLE, "BM25 recall", 1)

    assert hits[0].chunk is chunks[0]
    assert "[1] KTB4-13th-wiki/docs/ai/2.md:110-120@4f6a6a6" in text
    assert "제목: 문서 > 측정" in text
    assert "[문서 안내] 이 문서의 BM25 서술은 이력이다" in text
    # TABLE의 20행 결정이 docs/ai/2.md:117을 옛 서술로 적었다
    assert "[주의] 117행은 옛 서술이다. 지금 결정: 2026-09-24 ① 키워드 검색은" in text


def test_search_docs_truncates_long_chunks(monkeypatch):
    monkeypatch.setattr(core, "SNIPPET_CHARS", 10)
    chunks = [_chunk("docs/a.md", 1, 9, "배포 " * 20)]

    text, _ = search_docs(_search_index(chunks), None, "배포", 1)

    assert "…(이하 생략. 전체는 docs/a.md:1-9)" in text


def test_search_docs_without_query_or_hits():
    index = _search_index([_chunk("docs/a.md", 1, 2, "배포")])

    text, hits = search_docs(index, TABLE, "   ", 3, note="확인 실패")

    assert hits == []
    assert "알림: 확인 실패" in text
    assert "맞는 문서 조각이 없다" in text
