"""모든 기능의 단일 진입점 (implementation.md §2).

CLI와 MCP 서버는 여기 함수를 부르는 얇은 껍데기다.

MCP로 부를 때는 답 문장이 아니라 근거를 돌려준다 (plan.md D17): 결정 상태, `레포/경로:줄@커밋`
인용, 인덱스 기준 커밋. 답은 부르는 쪽(Claude Code)이 쓴다.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

from tka.decisions.table import Decision, DecisionTable
from tka.retrieve.search import DEFAULT_SETTINGS, Hit, SearchIndex, SearchSettings

# 검색어 끝의 조사. "피드는", "LangChain을" 같은 말도 찾게 떼어 낸다.
_PARTICLE = re.compile(r"(으로|에서|까지|부터|이랑|은|는|이|가|을|를|에|의|로|와|과|도|만)$")
_SPLIT = re.compile(r"[\s,?!.]+")


def find_decisions(
    table: DecisionTable,
    *,
    query: str = "",
    part: str = "",
    limit: int = 10,
    aliases: Sequence[Sequence[str]] = (),
    include_superseded: bool = False,
) -> list[Decision]:
    """결정을 찾는다. 검색어 개념이 많이 맞는 순, 같으면 최근 순.

    검색어가 비면 최근 결정부터. 별칭 묶음(예: ④·feed·피드)은 서로 바꿔 찾는다.
    대체된 결정은 기본으로 빼고(D17), 그걸 대체한 결정 밑에 "대체한 이전 결정"으로 보여 준다.
    """
    candidates = _newest_first(
        d
        for d in table.decisions
        if _part_matches(d, part) and (include_superseded or d.status != "대체됨")
    )
    concepts = _concepts(query, aliases)
    if concepts:
        score = {d.line: sum(_hit(group, d) for group in concepts) for d in candidates}
        # 안정 정렬이라 점수가 같으면 최근 순이 유지된다
        candidates = sorted((d for d in candidates if score[d.line]), key=lambda d: -score[d.line])
    return candidates[: max(limit, 1)]


def render_decisions(
    table: DecisionTable,
    decisions: Sequence[Decision],
    *,
    query: str = "",
    part: str = "",
    note: str | None = None,
) -> str:
    """MCP·CLI가 돌려줄 근거 글."""
    scope = " · ".join(
        x for x in (f"검색어 '{query}'" if query else "", f"파트 {part}" if part else "") if x
    )
    head = (
        f"결정 로그 기준 ({table.repo}@{table.commit[:7]}, 전체 {len(table.decisions)}개"
        f"{', ' + scope if scope else ''}). 로그에 없는 결정은 여기 나오지 않는다."
    )
    lines = [head]
    if note:
        lines.append(f"알림: {note}")
    if not decisions:
        lines.append(
            "맞는 결정이 없다. 결정 로그에 없는 결정일 수 있으니 관련 문서를 직접 확인한다. "
            "검색어 없이 부르면 최근 결정 전체를 본다."
        )
        return "\n".join(lines)
    by_line = {d.line: d for d in table.decisions}
    for d in decisions:
        lines.append("")
        lines.append(f"[{d.status}] {d.date} {d.part} — {d.text}")
        detail = d.detail_path or d.detail_note or "없음"
        affected = ", ".join(d.affected) or "-"
        lines.append(f"  영향 파트: {affected} · 상세: {detail}")
        if d.replaces:
            lines.append(f"  이전 결정: {d.replaces}")
        if d.old_text_at:
            lines.append(f"  옛 서술이 아직 남은 곳: {', '.join(d.old_text_at)}")
        if d.superseded_by and d.superseded_by in by_line:
            newer = by_line[d.superseded_by]
            lines.append(f"  대체한 결정: {newer.date} {newer.text}")
        for older in (o for o in table.decisions if o.superseded_by == d.line):
            lines.append(f"  대체한 이전 결정: {older.date} {older.text} (로그 {older.line}행)")
        lines.append(f"  근거: {table.citation(d)}")
    return "\n".join(lines)


def get_decision(
    table: DecisionTable,
    query: str = "",
    part: str = "",
    limit: int = 10,
    *,
    aliases: Sequence[Sequence[str]] = (),
    include_superseded: bool = False,
    note: str | None = None,
) -> tuple[str, list[Decision]]:
    decisions = find_decisions(
        table,
        query=query,
        part=part,
        limit=limit,
        aliases=aliases,
        include_superseded=include_superseded,
    )
    return render_decisions(table, decisions, query=query, part=part, note=note), decisions


SNIPPET_CHARS = 1500  # 조각 하나를 돌려줄 때의 상한. 넘으면 줄 범위를 알려 주고 자른다


def search_docs(
    index: SearchIndex,
    table: DecisionTable | None,
    query: str,
    k: int = 5,
    *,
    settings: SearchSettings = DEFAULT_SETTINGS,
    note: str | None = None,
) -> tuple[str, list[Hit]]:
    """문서 조각을 찾아 근거를 돌려준다 (plan.md D17).

    조각에 결정으로 바뀐 옛 서술(보정 파일 old_text_at)이 들어 있으면 [주의]로 지금 결정을 붙인다.
    조각 전체가 폐기된 것은 아니라서(같은 조각에 지금도 맞는 내용이 섞여 있다) 빼지 않고 알린다.
    """
    hits = index.hybrid(query, k, settings.weights) if query.strip() else []
    commit = hits[0].chunk.commit[:7] if hits else (table.commit[:7] if table else "?")
    lines = [
        f"문서 검색 결과 (기준 커밋 {commit}, 상위 {len(hits)}개, {settings.label()}). "
        "답 문장은 이 근거를 읽고 직접 쓴다. 결정이 바뀌었는지는 get_decision으로 함께 확인한다."
    ]
    if note:
        lines.append(f"알림: {note}")
    if not hits:
        lines.append("맞는 문서 조각이 없다. 검색어를 바꾸거나 get_decision으로 결정 로그를 본다.")
        return "\n".join(lines), hits

    old_text = _old_text_by_path(table)
    for rank, hit in enumerate(hits, 1):
        c = hit.chunk
        lines += ["", f"[{rank}] {c.repo}/{c.path}:{c.start_line}-{c.end_line}@{c.commit[:7]}"]
        lines.append(f"제목: {' > '.join((c.doc_title, *c.heading_path))}")
        if c.callout:
            lines.append(f"[문서 안내] {c.callout}")
        for line, d in old_text.get(c.path, []):
            if c.start_line <= line <= c.end_line:
                lines.append(
                    f"[주의] {line}행은 옛 서술이다. 지금 결정: {d.date} {d.text} "
                    f"(결정 로그 {d.line}행)"
                )
        text = c.text
        if len(text) > SNIPPET_CHARS:
            text = (
                f"{text[:SNIPPET_CHARS]}\n…(이하 생략. 전체는 {c.path}:{c.start_line}-{c.end_line})"
            )
        lines += ["---", text]
    return "\n".join(lines), hits


def _old_text_by_path(table: DecisionTable | None) -> dict[str, list[tuple[int, Decision]]]:
    out: dict[str, list[tuple[int, Decision]]] = {}
    for d in table.decisions if table else ():
        for ref in d.old_text_at:
            path, _, line = ref.rpartition(":")
            if line.isdigit():
                out.setdefault(path, []).append((int(line), d))
    return out


def _concepts(query: str, aliases: Sequence[Sequence[str]]) -> list[tuple[str, ...]]:
    """검색어를 개념 묶음으로. 별칭이 들어 있으면 그 묶음 전체, 나머지 낱말은 조사를 뗀 그대로."""
    lowered = query.lower()
    groups = [tuple(group) for group in aliases if any(a.lower() in lowered for a in group)]
    covered = {a.lower() for group in groups for a in group}
    for word in _SPLIT.split(query):
        stem = _strip_particle(word)
        if len(stem) < 2 or any(stem.lower() in c or c in stem.lower() for c in covered):
            continue
        groups.append((stem,))
    return groups


def _strip_particle(word: str) -> str:
    stem = _PARTICLE.sub("", word)
    return stem if len(stem) >= 2 else word


def _hit(group: tuple[str, ...], d: Decision) -> bool:
    haystack = " ".join(
        x for x in (d.part, d.text, *d.affected, d.detail_title, d.detail_path, d.replaces) if x
    ).lower()
    return any(term.lower() in haystack for term in group)


def _part_matches(d: Decision, part: str) -> bool:
    if not part:
        return True
    wanted = part.strip().lower()
    parts = {p.strip().lower() for p in re.split(r"[·,/]", d.part)} | {
        a.lower() for a in d.affected
    }
    return wanted in parts


def _newest_first(decisions) -> list[Decision]:
    """최근 날짜 먼저. 같은 날이면 로그 위쪽(먼저 적힌 줄) 먼저."""
    return sorted(sorted(decisions, key=lambda d: d.line), key=lambda d: d.date, reverse=True)
