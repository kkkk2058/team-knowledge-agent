import pytest

from tka.ingest import chunk as chunking
from tka.ingest.chunk import ChunkError, chunk_file, coverage_problems
from tka.ingest.files import SourceFile

FILE = SourceFile("wiki", "KTB4-13th-wiki", "abc1234def", "docs/x.md")


def _chunks(text: str):
    return chunk_file(FILE, text)


def _titles(chunks) -> list[str]:
    return [" > ".join(c.heading_path) for c in chunks]


DOC = """---
wiki: AI-5 컨텍스트 보강 설계
type: design
status: 작성중
updated: 2026-09-21
---
**요약** 두 가지 RAG를 정리한다.

> `book_passage`는 폐기했다.
> 아래 §2-A는 이력 참고용이다.

# AI-5 컨텍스트 보강 설계

## 1. 흐름

본문 하나.

```bash
# 이건 헤딩이 아니다
echo hi
```

### 1-1. 세부

세부 본문.

## 2. 빈 구역

## 3. 마지막

끝.
"""


def test_frontmatter_title_and_line_numbers():
    doc, chunks = _chunks(DOC)

    assert (doc.title, doc.doc_type, doc.status, doc.updated) == (
        "AI-5 컨텍스트 보강 설계",
        "design",
        "작성중",
        "2026-09-21",
    )
    intro = chunks[0]
    assert (intro.start_line, intro.end_line) == (7, 10)
    assert intro.text.startswith("**요약** 두 가지 RAG를 정리한다.")
    assert "wiki:" not in intro.text  # frontmatter는 본문에 없다


def test_headings_make_the_path_and_code_hash_is_not_a_heading():
    _, chunks = _chunks(DOC)

    assert _titles(chunks) == ["", "1. 흐름", "1. 흐름 > 1-1. 세부", "3. 마지막"]
    assert "# 이건 헤딩이 아니다" in chunks[1].text  # 코드 블록 안 # 은 본문이다


def test_section_chunk_starts_at_its_heading_line():
    _, chunks = _chunks(DOC)
    lines = DOC.split("\n")

    assert lines[chunks[1].start_line - 1] == "## 1. 흐름"
    assert lines[chunks[2].start_line - 1] == "### 1-1. 세부"


def test_callout_before_first_heading_is_on_every_chunk():
    doc, chunks = _chunks(DOC)

    assert doc.callout == "`book_passage`는 폐기했다. 아래 §2-A는 이력 참고용이다."
    assert all(c.callout == doc.callout for c in chunks)
    assert "[문서 안내] `book_passage`는 폐기했다" in chunks[2].context_text()


def test_context_text_and_id():
    _, chunks = _chunks(DOC)
    c = chunks[2]

    assert c.id == f"KTB4-13th-wiki/docs/x.md:{c.start_line}-{c.end_line}"
    assert c.context_text().startswith("AI-5 컨텍스트 보강 설계 > 1. 흐름 > 1-1. 세부\n")


def test_every_body_line_is_in_exactly_one_chunk():
    _, chunks = _chunks(DOC)

    assert coverage_problems(DOC, chunks) == []


def test_coverage_reports_a_missing_line():
    _, chunks = _chunks(DOC)

    problems = coverage_problems(DOC, chunks[:-1])

    assert problems == ["31행이 어느 청크에도 없다: '끝.'"]


DETAILS = """### 상품 · 2개

<details>
<summary><code>GET /api/v1/items</code> — 상품 목록 조회</summary>

cursor 방식으로 조회한다. 권한: 공개

</details>

<details><summary>② <code>POST /x</code> — 둘째</summary>

본문 둘.
끝 문장
</details>

### 다음

다음 본문.
"""


def test_details_become_chunks_titled_by_summary():
    _, chunks = _chunks(DETAILS)

    assert _titles(chunks) == [
        "상품 · 2개 > GET /api/v1/items — 상품 목록 조회",
        "상품 · 2개 > ② POST /x — 둘째",
        "다음",
    ]
    assert [c.in_details for c in chunks] == [True, True, False]
    assert chunks[1].text == "본문 둘.\n끝 문장"  # 빈 줄 없이 붙은 </details>도 닫는다
    assert coverage_problems(DETAILS, chunks) == []


def test_heading_inside_details_keeps_the_details_in_path():
    text = "## A\n\n<details><summary>접힘</summary>\n\n### 안쪽\n\n내용\n\n</details>\n\n뒤\n"
    _, chunks = _chunks(text)

    assert _titles(chunks) == ["A > 접힘 > 안쪽", "A"]


def test_nbsp_and_nfd_are_normalized_without_moving_lines():
    nfd = "가"  # NFD '가'
    text = f"## 제목\n\n값 있음 {nfd}\n"
    _, chunks = _chunks(text)

    assert chunks[0].text == "## 제목\n\n값 있음 가".split("\n\n", 1)[1]
    assert (chunks[0].start_line, chunks[0].end_line) == (1, 3)


def test_big_table_is_split_between_rows_with_header(monkeypatch):
    monkeypatch.setattr(chunking, "MAX_CHARS", 60)
    rows = "\n".join(f"| {n:02d} | 행 {n} 내용입니다 |" for n in range(1, 9))
    text = f"## 표\n\n| 번호 | 내용 |\n|---|---|\n{rows}\n"
    _, chunks = _chunks(text)

    assert len(chunks) > 1
    assert all(c.text.startswith("| 번호 | 내용 |\n|---|---|\n") for c in chunks)
    body_rows = [line for c in chunks for line in c.text.split("\n")[2:]]
    assert body_rows == rows.split("\n")  # 행이 빠지거나 겹치지 않는다
    assert coverage_problems(text, chunks) == []


def test_big_list_is_split_between_items(monkeypatch):
    monkeypatch.setattr(chunking, "MAX_CHARS", 40)
    items = "\n".join(f"- 항목 {n}: 조금 긴 설명입니다" for n in range(1, 6))
    text = f"## 목록\n\n{items}\n"
    _, chunks = _chunks(text)

    assert len(chunks) > 1
    assert "\n".join(c.text for c in chunks) == items
    assert coverage_problems(text, chunks) == []


def test_big_code_block_stays_whole_and_is_flagged(monkeypatch):
    monkeypatch.setattr(chunking, "MAX_CHARS", 30)
    code = "\n".join(f"line_{n} = {n}" for n in range(10))
    text = f"## 코드\n\n```python\n{code}\n```\n"
    _, chunks = _chunks(text)

    assert len(chunks) == 1
    assert chunks[0].oversized


LABELS = """## ③ 챗봇

설명 문단.

**입력**

입력 필드 설명이 여기에 있다.

**출력 (200)**

출력 필드 설명이 여기에 있다.
"""


def test_labels_split_only_long_sections(monkeypatch):
    _, short = _chunks(LABELS)
    assert _titles(short) == ["③ 챗봇"]  # 짧으면 라벨에서 나누지 않는다

    monkeypatch.setattr(chunking, "MAX_CHARS", 40)
    _, long = _chunks(LABELS)
    assert _titles(long) == ["③ 챗봇", "③ 챗봇 > 입력", "③ 챗봇 > 출력 (200)"]
    assert long[1].text.startswith("**입력**")
    assert coverage_problems(LABELS, long) == []


def test_title_falls_back_to_h1_then_file_name():
    doc, _ = _chunks("# 제목 하나\n\n본문\n")
    assert doc.title == "제목 하나"

    doc, _ = _chunks("본문만\n")
    assert doc.title == "x"


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("---\nwiki: x\n본문\n", "frontmatter가 닫히지 않았다"),
        ("---\nwiki: [x\n---\n본문\n", "frontmatter YAML 오류"),
        ("---\n- a\n---\n본문\n", "frontmatter가 매핑이 아니다"),
    ],
)
def test_bad_frontmatter(text, message):
    with pytest.raises(ChunkError, match=message):
        _chunks(text)
