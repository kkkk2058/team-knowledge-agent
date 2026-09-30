"""문서를 검색 단위(청크)로 자른다 (implementation.md ②).

- 마크다운 파서로 읽는다. 정규식은 코드 블록 안의 `#`을 헤딩으로 착각한다.
- 헤딩과 `<details>`에서 자른다. `<summary>`가 그 청크의 제목이다.
  FS-2 엔드포인트 40개, FS-1 테이블 28개가 전부 `<details>` 안에 있다.
- 한 구역이 MAX_CHARS를 넘으면 블록 경계에서 더 나눈다. 굵은 라벨 문단(**입력**)이 있으면
  거기서 먼저 나누고 라벨을 제목 경로에 붙인다. 표·코드 블록·목록은 자르지 않는다.
- 첫 헤딩 앞 인용 블록은 문서 안내(콜아웃)다. 그 문서의 모든 청크에 붙인다.
  AI-5의 폐기 안내가 여기 있어서, 붙이지 않으면 "이력 참고용" 본문이 현재 사실처럼 검색된다.
- frontmatter는 문서 정보로 빼고 본문 청크에 넣지 않는다.
- 정규화(NFC, NBSP → 공백)는 줄 안에서만 한다. 줄 번호가 원문과 같아야 `경로:줄` 인용이 맞다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from tka.ingest.files import SourceFile

MAX_CHARS = 900  # 한 청크 본문의 목표 상한. 쪼갤 수 없는 블록 하나가 넘으면 그대로 두고 표시한다
_md = MarkdownIt("commonmark").enable("table")
_SUMMARY = re.compile(r"<summary>(.*?)</summary>", re.S | re.I)
_TAG = re.compile(r"<[^>]+>")
_DETAILS_TAG = re.compile(r"<(/?)details\b", re.I)


class ChunkError(ValueError):
    """문서를 읽을 수 없다 (frontmatter 형식 등)."""


@dataclass(frozen=True)
class Document:
    file: SourceFile
    title: str
    doc_type: str | None
    status: str | None  # frontmatter status는 작성 상태다. 결정 상태가 아니다 (plan.md §6)
    updated: str | None
    callout: str | None  # 첫 헤딩 앞 인용 블록


@dataclass(frozen=True)
class Chunk:
    repo: str
    commit: str
    path: str
    doc_title: str
    heading_path: tuple[str, ...]
    start_line: int  # 1부터, 원문 줄 번호
    end_line: int  # 포함
    text: str  # 정규화한 본문
    callout: str | None
    oversized: bool  # 쪼갤 수 없는 블록 하나가 MAX_CHARS를 넘는다
    in_details: bool  # <details> 안의 내용

    @property
    def id(self) -> str:
        return f"{self.repo}/{self.path}:{self.start_line}-{self.end_line}"

    def context_text(self) -> str:
        """검색·LLM에 넣을 글. 제목 경로와 문서 안내를 본문 앞에 붙인다."""
        head = " > ".join((self.doc_title, *self.heading_path))
        callout = f"\n[문서 안내] {self.callout}" if self.callout else ""
        return f"{head}{callout}\n\n{self.text}"


@dataclass
class _Block:
    start: int  # 0부터, 포함
    end: int  # 0부터, 제외
    text: str
    label: str | None = None  # 굵은 라벨 문단이면 그 글

    @property
    def size(self) -> int:
        return len(self.text)


def normalize_line(line: str) -> str:
    return unicodedata.normalize("NFC", line).replace(" ", " ")


def chunk_file(
    file: SourceFile, text: str, max_chars: int | None = None
) -> tuple[Document, list[Chunk]]:
    """max_chars를 안 주면 MAX_CHARS. 청크 크기 비교 실험에서 바꿔 본다."""
    limit = max_chars or MAX_CHARS
    lines = [normalize_line(line) for line in text.split("\n")]
    meta, body_start = _frontmatter(lines, file.path)
    # frontmatter 줄은 빈 줄로 바꿔 파서에 넘긴다. 그래야 토큰의 줄 번호가 원문과 같다.
    parse_lines = [""] * body_start + lines[body_start:]
    tokens = _md.parse("\n".join(parse_lines))

    title = str(meta.get("wiki") or "") or _first_h1(tokens) or Path(file.path).stem
    callout = _callout(tokens, parse_lines)
    doc = Document(
        file=file,
        title=title,
        doc_type=_str_or_none(meta.get("type")),
        status=_str_or_none(meta.get("status")),
        updated=_str_or_none(meta.get("updated")),
        callout=callout,
    )
    return doc, list(_chunks(doc, tokens, parse_lines, limit))


def chunk_files(
    files: list[SourceFile], directory: Path, max_chars: int | None = None
) -> tuple[list[Document], list[Chunk]]:
    docs, chunks = [], []
    for file in files:
        text = (directory / file.path).read_text(encoding="utf-8")
        doc, file_chunks = chunk_file(file, text, max_chars)
        docs.append(doc)
        chunks.extend(file_chunks)
    return docs, chunks


def coverage_problems(text: str, chunks: list[Chunk]) -> list[str]:
    """빠지거나 두 청크에 겹친 본문 줄.

    헤딩 줄·details 태그 줄·frontmatter·빈 줄은 청크에 없어도 된다.

    표를 행 사이에서 나눌 때 머리행은 글에 다시 붙지만 줄 범위는 겹치지 않는다.
    """
    lines = [normalize_line(line) for line in text.split("\n")]
    _, body_start = _frontmatter(lines, "")
    parse_lines = [""] * body_start + lines[body_start:]
    tokens = _md.parse("\n".join(parse_lines))
    exempt = set(range(body_start))
    for i, t in enumerate(tokens):
        if t.level != 0 or t.map is None:
            continue
        if t.type == "heading_open" or (t.type == "html_block" and _details_marks(t, tokens, i)):
            if t.type == "heading_open" or _html_leftover(t, parse_lines) is None:
                exempt.update(range(*t.map))

    owners: dict[int, list[str]] = {}
    for c in chunks:
        for n in range(c.start_line - 1, c.end_line):
            owners.setdefault(n, []).append(c.id)
    problems = []
    for n, line in enumerate(parse_lines):
        if not line.strip() or n in exempt:
            continue
        if n not in owners:
            problems.append(f"{n + 1}행이 어느 청크에도 없다: {line.strip()[:40]!r}")
        elif len(owners[n]) > 1:
            problems.append(f"{n + 1}행이 청크 {len(owners[n])}개에 겹친다")
    return problems


def _frontmatter(lines: list[str], path: str) -> tuple[dict, int]:
    """frontmatter를 읽고, 본문이 시작하는 줄(0부터)을 돌려준다."""
    if not lines or lines[0].strip() != "---":
        return {}, 0
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            try:
                meta = yaml.safe_load("\n".join(lines[1:i])) or {}
            except yaml.YAMLError as e:
                raise ChunkError(f"{path}: frontmatter YAML 오류: {e}") from e
            if not isinstance(meta, dict):
                raise ChunkError(f"{path}: frontmatter가 매핑이 아니다")
            return meta, i + 1
    raise ChunkError(f"{path}: frontmatter가 닫히지 않았다")


def _chunks(doc: Document, tokens: list[Token], lines: list[str], limit: int):
    # 제목 경로 스택: (종류, 헤딩 수준, 글). 종류는 heading 또는 details.
    stack: list[tuple[str, int, str]] = []
    blocks: list[_Block] = []

    def path() -> tuple[str, ...]:
        return tuple(text for _, _, text in stack)

    # 구역을 연 줄(헤딩·<details>). 그 구역의 첫 청크가 이 줄부터 시작해 인용에 제목이 들어간다.
    anchor: list[int | None] = [None]

    def flush():
        in_details = any(kind == "details" for kind, _, _ in stack)
        yield from _emit(doc, path(), blocks, in_details, anchor[0], limit)
        blocks.clear()
        anchor[0] = None

    for i, token in enumerate(tokens):
        if token.level != 0 or token.type.endswith("_close") or token.map is None:
            continue
        if token.type == "heading_open":
            yield from flush()
            level = int(token.tag[1:])
            text = _inline_text(tokens[i + 1])
            # 열린 details 안의 헤딩은 그 details 위의 헤딩을 건드리지 않는다
            while stack and stack[-1][0] == "heading" and stack[-1][1] >= level:
                stack.pop()
            # 문서 제목과 같은 H1은 경로에 두 번 넣지 않는다
            if not (level == 1 and text == doc.title):
                stack.append(("heading", level, text))
            anchor[0] = token.map[0]
            continue

        marks = _details_marks(token, tokens, i)
        if marks:
            if token.type == "paragraph_open":
                # `끝 문장 </details>`처럼 문단 끝에서 닫히면 문단을 먼저 넣고 닫는다
                block = _block(token, tokens, i, lines)
                if block:
                    blocks.append(block)
                leftover = None
            else:
                leftover = _html_leftover(token, lines)
            summary_text = _summary(token.content) if token.type == "html_block" else None
            if leftover and "open" not in marks:
                blocks.append(leftover)  # 닫는 태그 앞에 붙은 본문
                leftover = None
            for n, mark in enumerate(marks):
                yield from flush()
                if mark == "open":
                    stack.append(("details", 0, summary_text or "(접힌 내용)"))
                    anchor[0] = token.map[0]
                    if leftover and "open" not in marks[n + 1 :]:
                        blocks.append(leftover)  # 태그와 같은 블록에 붙은 본문
                        leftover = None
                else:
                    while stack and stack.pop()[0] != "details":
                        pass
            continue

        blocks.extend(_blocks(token, tokens, i, lines, limit))
    yield from flush()


def _emit(
    doc: Document,
    path: tuple[str, ...],
    blocks: list[_Block],
    in_details: bool,
    anchor: int | None,
    limit: int,
):
    if not blocks:
        return
    total = sum(b.size for b in blocks)
    parts: list[tuple[str | None, list[_Block]]] = []
    label: str | None = None
    current: list[_Block] = []
    size = 0
    for b in blocks:
        starts_label = total > limit and b.label is not None
        too_big = current and size + b.size > limit
        if current and (starts_label or too_big):
            parts.append((label, current))
            current, size = [], 0
        if b.label is not None and total > limit:
            label = b.label
        current.append(b)
        size += b.size
    parts.append((label, current))

    for n, (label, part) in enumerate(parts):
        start = part[0].start if n > 0 or anchor is None else min(anchor, part[0].start)
        yield Chunk(
            repo=doc.file.repo,
            commit=doc.file.commit,
            path=doc.file.path,
            doc_title=doc.title,
            heading_path=path + ((label,) if label else ()),
            start_line=start + 1,
            end_line=part[-1].end,
            text="\n\n".join(b.text for b in part),
            callout=doc.callout,
            oversized=any(b.size > limit for b in part),
            in_details=in_details,
        )


def _blocks(
    token: Token, tokens: list[Token], i: int, lines: list[str], limit: int
) -> list[_Block]:
    """블록 하나. 너무 긴 표는 행 사이에서(머리행을 다시 붙여), 긴 목록은 항목 사이에서 나눈다.

    행이나 항목 중간은 자르지 않는다. 코드 블록은 나누지 않는다.
    """
    block = _block(token, tokens, i, lines)
    if block is None or block.size <= limit:
        return [block] if block else []
    if token.type == "table_open":
        return _split_table(token, lines, limit)
    if token.type in ("bullet_list_open", "ordered_list_open"):
        return _split_list(token, tokens, i, lines, limit)
    return [block]


def _split_table(token: Token, lines: list[str], limit: int) -> list[_Block]:
    start, end = token.map
    header = "\n".join(lines[start : start + 2])  # 머리행 + 구분행
    parts: list[_Block] = []
    group_start, rows = start, []
    for r in range(start + 2, end):
        row = lines[r]
        if rows and len(header) + sum(len(x) + 1 for x in rows) + len(row) > limit:
            parts.append(_Block(group_start, r, "\n".join([header, *rows])))
            group_start, rows = r, []
        rows.append(row)
    if rows:
        parts.append(_Block(group_start, end, "\n".join([header, *rows])))
    return parts


def _split_list(
    token: Token, tokens: list[Token], i: int, lines: list[str], limit: int
) -> list[_Block]:
    items = []
    for t in tokens[i + 1 :]:
        if t.level == token.level and t.type == token.type.replace("_open", "_close"):
            break
        if t.type == "list_item_open" and t.level == token.level + 1 and t.map:
            items.append(t.map)
    parts: list[_Block] = []
    group: list[list[int]] = []
    for item in items:
        size = sum(len("\n".join(lines[a:b])) for a, b in group)
        if group and size + len("\n".join(lines[item[0] : item[1]])) > limit:
            parts.append(_list_part(group, lines))
            group = []
        group.append(item)
    if group:
        parts.append(_list_part(group, lines))
    return parts


def _list_part(group: list[list[int]], lines: list[str]) -> _Block:
    start, end = group[0][0], group[-1][1]
    return _Block(start, end, "\n".join(lines[start:end]).strip())


def _block(token: Token, tokens: list[Token], i: int, lines: list[str]) -> _Block | None:
    start, end = token.map
    text = "\n".join(lines[start:end]).strip()
    if not text:
        return None
    label = None
    if token.type == "paragraph_open":
        label = _label(tokens[i + 1])
    return _Block(start, end, text, label)


def _label(inline: Token) -> str | None:
    """문단 전체가 굵은 글씨 하나면(**입력**) 라벨로 본다."""
    # markdown-it은 굵은 글씨 앞뒤에 빈 text 토큰을 둔다
    kids = [
        c
        for c in inline.children or []
        if c.type != "softbreak" and not (c.type == "text" and not c.content.strip())
    ]
    if len(kids) >= 3 and kids[0].type == "strong_open" and kids[-1].type == "strong_close":
        if not any(c.type == "strong_open" for c in kids[1:-1]):
            return _inline_text(inline)
    return None


def _details_marks(token: Token, tokens: list[Token], i: int) -> list[str]:
    """이 블록의 <details> 열기·닫기를 나온 순서대로. 예: ["open"], ["close"], ["open", "close"]"""
    if token.type == "html_block":
        html = token.content
    elif token.type == "paragraph_open":
        html = "".join(c.content for c in tokens[i + 1].children or [] if c.type == "html_inline")
    else:
        return []
    return ["close" if m.group(1) else "open" for m in _DETAILS_TAG.finditer(html)]


def _html_leftover(token: Token, lines: list[str]) -> _Block | None:
    """details·summary 태그를 걷어내고 남은 본문. 빈 줄 없이 태그에 붙어 쓴 글을 잃지 않으려고."""
    start, end = token.map
    raw = "\n".join(lines[start:end])
    rest = _TAG.sub("", _SUMMARY.sub("", raw)).strip()
    return _Block(start, end, rest) if rest else None


def _summary(html: str) -> str | None:
    m = _SUMMARY.search(html)
    if not m:
        return None
    return " ".join(_TAG.sub("", m.group(1)).split()) or None


def _callout(tokens: list[Token], lines: list[str]) -> str | None:
    quotes = []
    for token in tokens:
        if token.level != 0 or token.map is None:
            continue
        if token.type == "heading_open":
            break
        if token.type == "blockquote_open":
            start, end = token.map
            quote = " ".join(line.lstrip("> ").strip() for line in lines[start:end])
            quotes.append(" ".join(quote.split()))
    return " ".join(quotes) or None


def _first_h1(tokens: list[Token]) -> str | None:
    for i, token in enumerate(tokens):
        if token.type == "heading_open" and token.tag == "h1":
            return _inline_text(tokens[i + 1])
    return None


def _inline_text(inline: Token) -> str:
    parts = []
    for c in inline.children or []:
        if c.type in ("text", "code_inline"):
            parts.append(c.content)
        elif c.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return " ".join("".join(parts).split())


def _str_or_none(value) -> str | None:
    return None if value is None else str(value)
