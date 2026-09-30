"""결정 로그를 결정 표로 만든다 (implementation.md ③).

로그는 `| 날짜 | 파트 | 결정 | 영향 파트 | 상세 |` 표다.

- 날짜에 연도가 없어서(09-21) 설정의 decision_log_year를 붙인다.
- 상세 링크는 상대 경로라(../ai/…) 레포 루트 기준으로 풀고, 실제 파일인지 확인한다.
  링크 없이 "클라우드 확인 대기"처럼 글만 있는 칸도 있다.
- 번복 관계는 표에 칸이 없어서 보정 파일(config/*-supersedes.yaml)로 붙인다.
- 문제(풀리지 않는 링크, 맞는 행이 없는 보정)는 멈추지 않고 모아 알린다. 결정 표는 부분이라도
  쓸모가 있고, 무엇이 틀렸는지는 검사(`python -m tka.decisions check`)가 보여준다.
"""

from __future__ import annotations

import posixpath
import re
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import unquote

import yaml
from markdown_it import MarkdownIt
from markdown_it.token import Token

from tka.config import Config, Source
from tka.ingest.fetch import FetchError, checkout_problem, git, source_dir, tracked_files
from tka.ingest.files import read_lines

HEADER = ("날짜", "파트", "결정", "영향 파트", "상세")
DATE_PATTERN = re.compile(r"(\d{2})-(\d{2})")
_md = MarkdownIt("commonmark").enable("table")


class DecisionError(ValueError):
    """결정 표를 만들 수 없다 (로그에 표가 없음, 보정 파일 형식 오류)."""


@dataclass(frozen=True)
class Decision:
    date: str  # 2026-09-21
    part: str
    text: str
    affected: tuple[str, ...]
    detail_path: str | None  # 레포 루트 기준. 링크가 없으면 None
    detail_title: str | None  # 링크 글자
    detail_note: str | None  # 링크 없는 칸의 글 ("클라우드 확인 대기")
    line: int  # 로그의 줄 번호
    status: str = "현행"  # 현행 · 대체됨
    replaces: str | None = None  # 이 결정이 뒤집은 이전 결정
    old_text_at: tuple[str, ...] = ()  # 옛 서술이 남아 있는 곳 (경로:줄)
    superseded_by: int | None = None  # 대체한 행의 줄 번호


@dataclass(frozen=True)
class DecisionTable:
    repo: str
    commit: str
    log_path: str
    decisions: tuple[Decision, ...]
    problems: tuple[str, ...]

    def citation(self, d: Decision) -> str:
        return f"{self.repo}/{self.log_path}:{d.line}@{self.commit[:7]}"


@dataclass(frozen=True)
class Correction:
    date: str  # MM-DD
    contains: str
    replaces: str | None
    old_text_at: tuple[str, ...]
    superseded_by: tuple[str, str] | None  # (MM-DD, contains)


# ── 로그 파싱 ─────────────────────────────────────────────────────


def parse_decision_log(
    text: str, *, log_path: str, year: int, tracked: set[str]
) -> tuple[list[Decision], list[str]]:
    tokens = _md.parse(text)
    rows = _table_rows(tokens, log_path)
    decisions, problems = [], []
    for line, cells in rows:
        where = f"{log_path}:{line}"
        # GFM 표는 모자란 칸을 빈 칸으로 채우고 넘치는 칸은 버린다. 그래서 칸 수 대신 빈 칸을 본다.
        (date_raw, _), (part, _), (decision, _), (affected, _), (detail, detail_token) = cells
        if not part or not decision:
            problems.append(f"{where}: 파트나 결정 칸이 비어 있다")
            continue
        m = DATE_PATTERN.fullmatch(date_raw)
        if not m:
            problems.append(f"{where}: 날짜가 MM-DD가 아니다: {date_raw!r}")
            continue
        detail_path, detail_title, detail_note = None, None, None
        href, link_text = _first_link(detail_token)
        if href is None:
            detail_note = detail or None
        else:
            detail_title = link_text
            detail_path = _resolve(log_path, href)
            if detail_path is None:
                problems.append(f"{where}: 상세 링크가 레포 밖을 가리킨다: {unquote(href)}")
            elif detail_path not in tracked:
                problems.append(f"{where}: 상세 링크가 실제 파일로 풀리지 않는다: {unquote(href)}")
        decisions.append(
            Decision(
                date=f"{year}-{m.group(1)}-{m.group(2)}",
                part=part,
                text=decision,
                affected=_split_affected(affected),
                detail_path=detail_path,
                detail_title=detail_title,
                detail_note=detail_note,
                line=line,
            )
        )
    return decisions, problems


def _table_rows(tokens: list[Token], log_path: str) -> list[tuple[int, list[tuple[str, Token]]]]:
    """결정 로그 머리행을 가진 표의 행들: (줄 번호, [(칸 글, 칸 inline 토큰)])."""
    rows: list[tuple[int, list[tuple[str, Token]]]] = []
    header: list[str] = []
    in_table = in_head = False
    row: list[tuple[str, Token]] = []
    row_line = 0
    for i, t in enumerate(tokens):
        if t.type == "table_open":
            in_table, header = True, []
        elif t.type == "table_close":
            if tuple(header) == HEADER:
                return rows
            in_table, rows = False, []
        elif not in_table:
            continue
        elif t.type == "thead_open":
            in_head = True
        elif t.type == "thead_close":
            in_head = False
        elif t.type == "tr_open":
            row, row_line = [], (t.map[0] + 1 if t.map else 0)
        elif t.type in ("th_open", "td_open"):
            inline = tokens[i + 1]
            text = _inline_text(inline)
            if in_head:
                header.append(text)
            else:
                row.append((text, inline))
        elif t.type == "tr_close" and not in_head:
            rows.append((row_line, row))
    raise DecisionError(f"{log_path}: 머리행이 {' | '.join(HEADER)}인 표가 없다")


def _inline_text(inline: Token) -> str:
    parts = []
    for c in inline.children or []:
        if c.type == "text":
            parts.append(c.content)
        elif c.type == "code_inline":
            parts.append(f"`{c.content}`")
        elif c.type in ("softbreak", "hardbreak"):
            parts.append(" ")
    return " ".join("".join(parts).split())


def _first_link(inline: Token) -> tuple[str | None, str | None]:
    children = inline.children or []
    for i, c in enumerate(children):
        if c.type == "link_open":
            text = []
            for d in children[i + 1 :]:
                if d.type == "link_close":
                    break
                if d.type in ("text", "code_inline"):
                    text.append(d.content)
            return str(c.attrs.get("href", "")), "".join(text) or None
    return None, None


def _resolve(log_path: str, href: str) -> str | None:
    """로그 기준 상대 링크를 레포 루트 기준 경로로. 레포 밖이면 None."""
    # 파서가 한글 경로를 %EC%97…로 바꿔 두므로 되돌린다
    target = unquote(href.split("#", 1)[0])
    if not target or "://" in target:
        return None
    path = posixpath.normpath(posixpath.join(posixpath.dirname(log_path), target))
    return None if path.startswith("../") or path == ".." else path


def _split_affected(raw: str) -> tuple[str, ...]:
    if raw.strip() in ("", "-", "—"):
        return ()
    return tuple(p.strip() for p in raw.split(",") if p.strip())


# ── 보정 파일 ─────────────────────────────────────────────────────


def load_corrections(path: Path) -> list[Correction]:
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise DecisionError(f"{path}: YAML 문법 오류: {e}") from e
    items = raw.get("corrections") or []
    if not isinstance(items, list):
        raise DecisionError(f"{path}: corrections는 목록이어야 한다")
    corrections = []
    for n, item in enumerate(items):
        where = f"{path.name} corrections[{n}]"
        if not isinstance(item, dict):
            raise DecisionError(f"{where}: 매핑이 아니다")
        date, contains = str(item.get("date") or ""), str(item.get("contains") or "")
        if not DATE_PATTERN.fullmatch(date) or not contains:
            raise DecisionError(f"{where}: date(MM-DD)와 contains가 있어야 한다")
        by = item.get("superseded_by")
        if by is not None and not (isinstance(by, dict) and by.get("date") and by.get("contains")):
            raise DecisionError(f"{where}.superseded_by: date와 contains가 있어야 한다")
        old = item.get("old_text_at") or []
        if not isinstance(old, list) or not all(isinstance(x, str) for x in old):
            raise DecisionError(f"{where}.old_text_at: '경로:줄' 목록이어야 한다")
        corrections.append(
            Correction(
                date=date,
                contains=contains,
                replaces=item.get("replaces"),
                old_text_at=tuple(old),
                superseded_by=(str(by["date"]), str(by["contains"])) if by else None,
            )
        )
    return corrections


def apply_corrections(
    decisions: list[Decision],
    corrections: list[Correction],
    *,
    lines_of: dict[str, int],
) -> tuple[list[Decision], list[str]]:
    """보정을 붙인다. lines_of: 레포 파일 경로 → 줄 수 (old_text_at 검사용)."""
    problems = []
    by_line = {d.line: d for d in decisions}
    for c in corrections:
        label = f"보정 {c.date} '{c.contains}'"
        target = _find(decisions, c.date, c.contains)
        if isinstance(target, str):
            problems.append(f"{label}: {target}")
            continue
        changes: dict = {}
        if c.replaces:
            changes["replaces"] = c.replaces
        if c.old_text_at:
            changes["old_text_at"] = c.old_text_at
            for ref in c.old_text_at:
                problem = _check_ref(ref, lines_of)
                if problem:
                    problems.append(f"{label}: old_text_at {ref}: {problem}")
        if c.superseded_by:
            newer = _find(decisions, *c.superseded_by)
            if isinstance(newer, str):
                problems.append(f"{label}: superseded_by: {newer}")
            else:
                changes.update(status="대체됨", superseded_by=newer.line)
        by_line[target.line] = replace(by_line[target.line], **changes)
    return [by_line[d.line] for d in decisions], problems


def _find(decisions: list[Decision], date: str, contains: str) -> Decision | str:
    matches = [d for d in decisions if d.date.endswith(date) and contains in d.text]
    if len(matches) == 1:
        return matches[0]
    return "맞는 로그 행이 없다" if not matches else f"맞는 로그 행이 {len(matches)}개다"


def _check_ref(ref: str, lines_of: dict[str, int]) -> str | None:
    path, _, line = ref.rpartition(":")
    if not path or not line.isdigit():
        return "'경로:줄' 형식이 아니다"
    if path not in lines_of:
        return "기준 커밋에 없는 파일이다"
    if not 1 <= int(line) <= lines_of[path]:
        return f"줄 범위 밖이다 (파일은 {lines_of[path]}줄)"
    return None


# ── 조립 ──────────────────────────────────────────────────────────


def build_table(config: Config, root: Path, source: Source | None = None) -> DecisionTable:
    """설정대로 결정 표를 만든다. source를 주면 그 소스(예: 최신 main을 따르는 사본)에서 읽는다."""
    if config.decisions is None:
        raise DecisionError("설정에 decisions가 없다")
    source = source or next(s for s in config.sources if s.name == config.decisions.source)
    directory = source_dir(config, root, source)
    problem = checkout_problem(directory, source)
    if problem:
        raise FetchError(problem)
    tracked = tracked_files(directory)
    log_path = config.decisions.log
    if log_path not in tracked:
        raise DecisionError(f"{source.repo_name}: 결정 로그가 없다: {log_path}")

    text = (directory / log_path).read_text(encoding="utf-8")
    decisions, problems = parse_decision_log(
        text, log_path=log_path, year=config.decision_log_year, tracked=tracked
    )
    if config.decisions.corrections:
        corrections = load_corrections(root / config.decisions.corrections)
        referenced = {r.rpartition(":")[0] for c in corrections for r in c.old_text_at}
        lines_of = {p: len(read_lines(directory / p)) for p in referenced if p in tracked}
        decisions, more = apply_corrections(decisions, corrections, lines_of=lines_of)
        problems += more

    return DecisionTable(
        repo=source.repo_name,
        commit=git(directory, "rev-parse", "HEAD").strip(),
        log_path=log_path,
        decisions=tuple(decisions),
        problems=tuple(problems),
    )


def count_log_rows(text: str) -> int:
    """로그 표의 데이터 행 수. 결정 표 행 수와 맞춰 보는 데 쓴다 (완료 기준)."""
    return len(_table_rows(_md.parse(text), "로그"))
