"""근거 모으기 → LLM 답(문장마다 근거 ID) → 인용 검증 → 렌더링 (implementation.md ⑤).

- 근거는 두 가지다. 결정 [D]: 결정 표에서 찾은 현행 결정과 검색에 걸린 결정 행.
  문서 [C]: 검색에 걸린 문서 조각. 결정이 문서 본문보다 우선이다.
- 검색(RRF)이 놓쳐도 결정 표 찾기(별칭·조사 처리)가 잡는 질문이 있다(g19). 그래서 둘 다 넣는다.
- LLM은 문장과 근거 ID만 쓴다. 인용 문자열(`레포/경로:줄@커밋`)은 코드가 근거 정보로 만든다.
  그래서 없는 경로를 지어낼 수 없다. 넘기지 않은 근거 ID를 단 문장은 뺀다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from tka.answer.llm import LLMError, LLMResult, OpenRouter
from tka.core import find_decisions, old_text_by_path
from tka.decisions.table import Decision, DecisionTable
from tka.retrieve.search import DEFAULT_SETTINGS, SearchIndex, SearchSettings

K = 5  # 문서 조각 수
DECISIONS = 3  # 결정 표에서 더 넣을 결정 수
SNIPPET_CHARS = 1500

SCHEMA = {
    "type": "object",
    "properties": {
        "unknown": {"type": "boolean"},
        "sentences": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "sources": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "sources"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["unknown", "sentences"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = "\n".join(
    [
        "너는 북적북적(KTB4-13th) 팀 문서를 근거로 팀원 질문에 답한다.",
        "",
        "규칙",
        "1. [근거]에 있는 내용으로만 답한다. 근거에 없는 사실·이름·숫자를 지어내지 않는다.",
        "2. 문장마다 그 문장을 뒷받침하는 근거 ID를 sources에 적는다"
        '(예: ["D1", "C2"]). 근거 ID가 없는 문장은 쓰지 않는다. '
        "파일 경로와 줄 번호는 쓰지 않는다.",
        "3. [근거]로 질문에 답할 수 없으면 unknown을 true로 한다. "
        "그때 sentences에는 근거에 있는 관련 사실만 적거나 비운다.",
        "4. 결정([D…])이 있으면 그 결정을 지금 값으로 답한다. 문서 본문([C…])이 결정과 다른 "
        "값을 말하면 그 사실도 한 문장으로 알리고 양쪽 근거를 모두 단다.",
        "5. 이전 결정이나 옛 서술은 다음 경우에만 한 문장 덧붙인다: 질문이 옛 값을 전제할 때, "
        '근거에 [주의]나 "이전 결정"이 있어 헷갈릴 수 있을 때, 질문이 이유를 물을 때.',
        "6. 문서끼리 같은 것에 다른 값을 말하면 한쪽만 고르지 말고 둘 다 근거와 함께 알린다.",
        "7. 첫 문장에 질문에 대한 답을 쓴다. 짧게, 한국어로 쓴다.",
    ]
)


@dataclass(frozen=True)
class Source:
    id: str  # D1, C1 …
    kind: str  # decision · doc
    citation: str  # 레포/경로:줄@커밋 (코드가 만든다)
    title: str
    text: str  # 프롬프트에 넣은 글


@dataclass(frozen=True)
class Sentence:
    text: str
    sources: tuple[str, ...]


@dataclass(frozen=True)
class Answer:
    question: str
    unknown: bool
    sentences: tuple[Sentence, ...]  # 인용 검증을 통과한 문장
    dropped: tuple[Sentence, ...]  # 근거 ID가 없거나 넘기지 않은 ID만 단 문장
    sources: tuple[Source, ...]  # LLM에 넘긴 근거
    llm: LLMResult

    def render(self) -> str:
        used: list[str] = []
        for s in self.sentences:
            used += [i for i in s.sources if i not in used]
        number = {source_id: n for n, source_id in enumerate(used, 1)}
        body = [f"{s.text} {''.join(f'[{number[i]}]' for i in s.sources)}" for s in self.sentences]
        if self.unknown:
            head = "모름 — 근거 문서에서 질문에 대한 답을 찾지 못했다."
            body = [head, *(["관련해 문서에 있는 것:", *body] if body else [])]
        by_id = {s.id: s for s in self.sources}
        refs = [f"[{number[i]}] {by_id[i].citation} — {by_id[i].title}" for i in used]
        return "\n".join(body + (["", "근거:", *refs] if refs else []))


def gather_sources(
    index: SearchIndex,
    table: DecisionTable | None,
    question: str,
    *,
    settings: SearchSettings = DEFAULT_SETTINGS,
    aliases: Sequence[Sequence[str]] = (),
    k: int = K,
    decisions: int = DECISIONS,
) -> list[Source]:
    hits = index.hybrid(question, k, settings.weights)
    by_line = {d.line: d for d in table.decisions} if table else {}
    decision_lines: list[int] = []
    if table:
        found = find_decisions(table, query=question, limit=decisions, aliases=aliases)
        decision_lines += [d.line for d in found]
    decision_lines += [
        h.chunk.start_line
        for h in hits
        if h.chunk.kind == "decision" and h.chunk.start_line not in decision_lines
    ]

    sources: list[Source] = []
    for n, line in enumerate(decision_lines, 1):
        if line not in by_line or table is None:
            continue
        d = by_line[line]
        sources.append(
            Source(
                id=f"D{n}",
                kind="decision",
                citation=table.citation(d),
                title=f"결정 로그 {d.date} {d.part} ({d.status})",
                text=_decision_text(d, table),
            )
        )
    old_text = old_text_by_path(table)
    docs = [h.chunk for h in hits if h.chunk.kind != "decision"]
    for n, c in enumerate(docs, 1):
        lines = [f"제목: {' > '.join((c.doc_title, *c.heading_path))}"]
        if c.callout:
            lines.append(f"[문서 안내] {c.callout}")
        for line, d in old_text.get(c.path, []):
            if c.start_line <= line <= c.end_line:
                lines.append(f"[주의] {line}행은 옛 서술이다. 지금 결정: {d.date} {d.text}")
        text = c.text if len(c.text) <= SNIPPET_CHARS else c.text[:SNIPPET_CHARS] + "\n…(생략)"
        lines.append(text)
        sources.append(
            Source(
                id=f"C{n}",
                kind="doc",
                citation=f"{c.repo}/{c.path}:{c.start_line}-{c.end_line}@{c.commit[:7]}",
                title=" > ".join((c.doc_title, *c.heading_path)),
                text="\n".join(lines),
            )
        )
    return sources


def build_prompt(question: str, sources: Sequence[Source]) -> str:
    blocks = [f"[질문]\n{question}", "[근거]"]
    blocks += [f"[{s.id}] {s.text}" for s in sources] or ["(근거 없음)"]
    return "\n\n".join(blocks)


def verify(data: dict, allowed: set[str]) -> tuple[bool, list[Sentence], list[Sentence]]:
    """LLM 답을 검사한다. 넘긴 근거 ID만 남기고, 근거가 하나도 안 남은 문장은 뺀다."""
    unknown = data.get("unknown")
    sentences = data.get("sentences")
    if not isinstance(unknown, bool) or not isinstance(sentences, list):
        raise LLMError(f"답 형식이 스키마와 다르다: {str(data)[:200]}")
    kept, dropped = [], []
    for raw in sentences:
        text = str(raw.get("text", "")).strip() if isinstance(raw, dict) else ""
        ids = raw.get("sources", []) if isinstance(raw, dict) else []
        valid = tuple(dict.fromkeys(i for i in ids if isinstance(i, str) and i in allowed))
        if not text:
            continue
        (kept if valid else dropped).append(Sentence(text, valid or tuple(map(str, ids))))
    return unknown, kept, dropped


def answer_question(
    index: SearchIndex,
    table: DecisionTable | None,
    question: str,
    llm: OpenRouter,
    *,
    settings: SearchSettings = DEFAULT_SETTINGS,
    aliases: Sequence[Sequence[str]] = (),
    k: int = K,
    decisions: int = DECISIONS,
) -> Answer:
    sources = gather_sources(
        index, table, question, settings=settings, aliases=aliases, k=k, decisions=decisions
    )
    result = llm.complete_json(SYSTEM_PROMPT, build_prompt(question, sources), SCHEMA, "answer")
    unknown, kept, dropped = verify(result.data, {s.id for s in sources})
    return Answer(question, unknown, tuple(kept), tuple(dropped), tuple(sources), result)


def _decision_text(d: Decision, table: DecisionTable) -> str:
    lines = [f"결정 로그 · {d.date} {d.part} · {d.status}", d.text]
    if d.affected:
        lines.append(f"영향 파트: {', '.join(d.affected)}")
    if d.replaces:
        lines.append(f"이전 결정: {d.replaces}")
    for older in (o for o in table.decisions if o.superseded_by == d.line):
        lines.append(f"이 결정이 대체한 이전 결정: {older.date} {older.text}")
    if d.superseded_by:
        newer = next((n for n in table.decisions if n.line == d.superseded_by), None)
        if newer:
            lines.append(f"이 결정은 대체됐다. 지금 결정: {newer.date} {newer.text}")
    if d.old_text_at:
        lines.append(f"옛 서술이 아직 남은 곳: {', '.join(d.old_text_at)}")
    return "\n".join(lines)
