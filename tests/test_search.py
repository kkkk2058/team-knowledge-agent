import hashlib

import numpy as np
import pytest

from tka.golden import Evidence, GoldenItem, GoldenSet
from tka.index.keyword import BM25, tokenize_kiwi, tokenize_ngrams, tokenize_words
from tka.index.vector import Embedder
from tka.ingest.chunk import Chunk
from tka.retrieve.evaluate import (
    Variant,
    overlaps,
    render_report,
    run,
    summarize,
    targets,
)
from tka.retrieve.search import SearchIndex

# ── 토크나이저·BM25 ───────────────────────────────────────────────


def test_words_keep_particles_attached():
    assert tokenize_words("피드는 GET으로 부른다.") == ["피드는", "get으로", "부른다"]


def test_ngrams_keep_two_letter_words_and_split_longer_ones():
    assert tokenize_ngrams("배송 배송비") == ["배송", "배송", "송비", "배송비"]


def test_kiwi_drops_particles_and_endings():
    tokens = tokenize_kiwi("홈 추천 피드는 POST로 호출하나?")

    assert tokens == ["홈", "추천", "피드", "post", "호출"]


def test_bm25_ranks_the_document_with_rare_terms_first():
    docs = [["피드", "get"], ["검색", "pg_trgm"], ["피드", "검색", "공통"]]
    scores = BM25(docs).scores(["pg_trgm", "검색"])

    assert int(np.argmax(scores)) == 1
    assert scores[0] == 0  # 검색어가 없는 문서


def test_bm25_unknown_terms_score_zero():
    assert BM25([["a"], ["b"]]).scores(["없음"]).tolist() == [0, 0]


# ── 가짜 임베딩 모델 ──────────────────────────────────────────────


class FakeModel:
    """낱말마다 정해진 방향을 더한 벡터. 같은 낱말이 많을수록 가깝다."""

    def __init__(self) -> None:
        self.calls = 0

    def encode(self, texts, batch_size, normalize_embeddings, convert_to_numpy):
        self.calls += 1
        out = []
        for t in texts:
            v = np.zeros(64, dtype=np.float32)
            for word in tokenize_kiwi(t.split(": ", 1)[1]):
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % 64] += 1
            out.append(v / (np.linalg.norm(v) or 1))
        return np.array(out)


def _embedder(cache=None) -> tuple[Embedder, FakeModel]:
    embedder = Embedder(cache)
    model = FakeModel()
    embedder.__dict__["model"] = model  # cached_property 자리에 넣어 진짜 모델을 불러오지 않는다
    return embedder, model


def test_embedder_uses_prefixes_and_caches(tmp_path):
    embedder, model = _embedder(tmp_path / "index.sqlite")
    seen = []
    original = model.encode
    model.encode = lambda texts, **kw: seen.extend(texts) or original(texts, **kw)

    embedder.passages(["피드 설명", "검색 설명"])
    embedder.passages(["피드 설명"])  # 캐시에서 온다
    embedder.query("피드")

    assert seen == ["passage: 피드 설명", "passage: 검색 설명", "query: 피드"]

    again, again_model = _embedder(tmp_path / "index.sqlite")  # 파일 캐시는 프로세스를 넘어 남는다
    again.passages(["검색 설명"])
    assert again_model.calls == 0


# ── 색인·RRF ──────────────────────────────────────────────────────


def _chunk(path: str, start: int, end: int, text: str, heading=(), callout=None) -> Chunk:
    return Chunk(
        repo="wiki",
        commit="abc1234",
        path=path,
        doc_title="문서",
        heading_path=tuple(heading),
        start_line=start,
        end_line=end,
        text=text,
        callout=callout,
        oversized=False,
        in_details=False,
    )


CHUNKS = [
    _chunk("docs/a.md", 1, 5, "④ 피드는 GET으로 부른다", heading=("피드",)),
    _chunk("docs/a.md", 6, 9, "① 검색은 pg_trgm만 쓴다", heading=("검색",)),
    _chunk("docs/b.md", 1, 4, "추천 이유는 description으로 만든다", callout="book_passage 폐기"),
    _chunk("docs/b.md", 5, 8, "배포는 Recreate 전략이다", heading=("배포",)),
]


def _index(**kw) -> SearchIndex:
    embedder, _ = _embedder()
    return SearchIndex(CHUNKS, embedder, **kw)


def test_keyword_and_vector_find_the_right_chunk():
    index = _index()

    assert index.keyword("피드 메서드", 1)[0].chunk is CHUNKS[0]
    assert index.vector("검색 pg_trgm", 1)[0].chunk is CHUNKS[1]


def test_context_mode_searches_titles_and_callouts():
    assert _index(text_mode="context").keyword("book_passage", 1)[0].chunk is CHUNKS[2]
    assert _index(text_mode="body").keyword("book_passage", 1) == []  # 본문에는 없는 말


def test_hybrid_merges_both_rankings_by_rank():
    index = _index()
    hits = index.hybrid("피드 배포", 4)

    assert {h.chunk.path for h in hits[:2]} == {"docs/a.md", "docs/b.md"}
    assert all(a.score >= b.score for a, b in zip(hits, hits[1:], strict=False))


def test_hybrid_weights_favor_keyword():
    index = _index()
    even = index.hybrid("배포 전략", 4, (1, 1))
    keyword_heavy = index.hybrid("배포 전략", 4, (3, 1))

    assert keyword_heavy[0].chunk is index.keyword("배포 전략", 1)[0].chunk
    assert len(even) == len(keyword_heavy)


def test_bad_options_are_rejected():
    with pytest.raises(ValueError, match="text_mode"):
        SearchIndex(CHUNKS, None, text_mode="x")
    with pytest.raises(ValueError, match="tokenizer"):
        SearchIndex(CHUNKS, None, tokenizer="x")
    with pytest.raises(ValueError, match="임베더 없이"):
        SearchIndex(CHUNKS, None).vector("피드")


# ── 검색 평가 ─────────────────────────────────────────────────────


def _item(id_, type_, evidence) -> GoldenItem:
    return GoldenItem(
        id_, type_, "v0", f"{id_} 질문", "답", tuple(evidence), "확정", None, None, ()
    )


def test_overlaps_needs_same_file_and_lines():
    ev = Evidence("wiki", "docs/a.md", 3, 3)

    assert overlaps(CHUNKS[0], ev)
    assert not overlaps(CHUNKS[1], ev)  # 같은 파일, 다른 줄
    assert not overlaps(CHUNKS[2], Evidence("wiki", "docs/a.md", 1, 4))  # 다른 파일


def test_targets_skip_unknown_items_and_out_of_scope_evidence():
    golden = GoldenSet(
        {"wiki": "abc1234"},
        (
            _item("g01", "명세 값", [Evidence("wiki", "docs/a.md", 1, 1)]),
            _item("g02", "모름", []),
            _item("g03", "명세 값", [Evidence("wiki", "backup/x.md", 1, 1, outside_scope=True)]),
        ),
    )

    assert [(i.id, len(ev)) for i, ev in targets(golden)] == [("g01", 1)]


def test_run_scores_first_hit_and_coverage():
    golden = GoldenSet(
        {"wiki": "abc1234"},
        (
            _item("g01", "명세 값", [Evidence("wiki", "docs/a.md", 2, 2)]),
            _item(
                "g02",
                "문서끼리 다름",
                [Evidence("wiki", "docs/a.md", 7, 7), Evidence("wiki", "docs/b.md", 6, 6)],
            ),
        ),
    )
    index = _index()
    index_questions = {"g01 질문": "피드", "g02 질문": "검색 pg_trgm"}
    original = index.keyword
    index.keyword = lambda q, k=10: original(index_questions[q], k)

    results = run(index, Variant("키워드", "keyword"), golden)
    s = summarize(results)

    assert results[0].first_hit == 1 and results[0].covered == 1
    assert results[1].first_hit == 1 and results[1].covered == 1  # b.md 근거는 못 찾음
    assert (s.hits, s.full, s.n) == (2, 1, 2)
    assert s.recall() == "100% (2/2)" and s.full_recall() == "50% (1/2)"

    report = render_report(
        [(Variant("키워드", "keyword"), results)],
        meta={"소스": "wiki @ abc1234"},
        titles={CHUNKS[0].id: "문서 > 피드"},
        best="키워드",
    )
    assert "| 키워드 **(선택)** | 100% (2/2) | 50% (1/2) | 1.00 |" in report
    assert "| g01 | 명세 값 | 1 | 1/1 | 문서 > 피드 |" in report
