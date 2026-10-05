"""자동 생성 검증셋: 기준 커밋 문서에서 LLM이 질문·정답·근거 줄을 만든다 (plan.md D18).

    uv run python -m tka.bigset generate [--model M]   eval/bigset.yaml을 만든다
    uv run python -m tka.bigset review [--n 20]        레포 주인이 확인할 표본 시트 → data/

골든셋 20문항은 튜닝용이라 넓이를 재지 못한다. 그래서 수백 문항을 자동으로 만든다.

- 질문을 만드는 모델은 봇(Gemini)·베이스라인(Claude)과 다른 계열이다. 한쪽에 유리하지 않게.
- 구절은 봇의 청크와 상관없이 고른다: 문서마다 크기에 비례해 30줄 창을 무작위로 뽑는다.
  봇의 청크 경계와 맞춰 뽑으면 검색이 실제보다 좋아 보인다.
- 세 종류를 만든다. ① 구절 → 사실·이유 질문 ② 결정 로그 행 → 지금 결정 질문(뒤집힌 결정은 함정)
  ③ 문서에 없는 것 → 모름 질문(없음 확인 단어는 코드가 문서 전체에서 검사한다).
- 근거 줄은 코드가 검사한다: 뽑은 창 안이고, 비어 있지 않고, 10줄 이하.
- 문항을 dev·test로 반씩 나눈다(유형별로). dev는 틀린 이유를 보고 고쳐도 되고, test는 합계만 본다.
- 한계: 사람 질문보다 문서 표현을 닮아 점수가 후할 수 있다. "문서끼리 다름"은 만들지 못한다.
  정답은 레포 주인이 표본만 확인한다. 최종 비교는 팀 질문으로 한다.
"""

from __future__ import annotations

import argparse
import random
import re
import sys
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tka.answer.llm import LLMError, OpenRouter, load_api_key
from tka.config import Config, Source, load_config
from tka.decisions.table import DecisionTable, build_table
from tka.evaluation import dump_yaml
from tka.golden import GoldenSet, load_golden, render_review
from tka.index.keyword import tokenize_kiwi
from tka.ingest.fetch import source_dir
from tka.ingest.files import read_lines, select_files

MODEL = "openai/gpt-6.1-sol"
TEMPERATURE = None  # OpenAI 모델은 temperature를 받지 않는다 (tka.answer.llm)
SEED = 20260930
WINDOW_LINES = 30
CHARS_PER_QUESTION = 2400  # 문서 39만 자 → 구절 질문 약 150개
MAX_PER_DOC = 14
MAX_EVIDENCE_LINES = 10
UNKNOWN_QUESTIONS = 25
BASELINE_SAMPLE = 50
WORKERS = 8
DUPLICATE_OVERLAP = 0.7  # 두 질문의 형태소가 이만큼 겹치면 같은 질문으로 본다

PASSAGE_TYPES = ("명세 값", "정책 값", "결정 이유", "장애 동작", "클라우드", "팀 규칙")
HEADING = re.compile(r"^(#{1,6})\s+(.*)")

STYLE_RULES = [
    "질문은 팀원이 메신저로 동료에게 묻듯 짧은 한국어 한 문장으로 쓴다.",
    "문서 제목·헤딩 문구를 그대로 베끼지 말고 다른 말로 묻는다. API 경로·테이블·필드 이름처럼 "
    "꼭 필요한 식별자는 그대로 써도 된다.",
    '"이 문서", "위 표", "여기서" 같은 말을 쓰지 않는다. '
    "질문만 보고 무엇을 묻는지 알 수 있어야 한다.",
]

PASSAGE_SYSTEM = "\n".join(
    [
        "너는 북적북적(KTB4-13th) 팀 문서로 검증용 질문을 만든다. 구절 하나로 질문 하나를 만든다.",
        "",
        "규칙",
        *[f"{n}. {rule}" for n, rule in enumerate(STYLE_RULES, 1)],
        "4. 답은 [구절]의 근거 줄만 읽고 하나로 정해져야 한다. 근거 줄 번호를 evidence_start·"
        f"evidence_end로 적는다({MAX_EVIDENCE_LINES}줄 이하).",
        "5. expected에는 정답을 한두 문장으로 쓴다. 근거에 없는 내용을 넣지 않는다.",
        "6. 구절이 목차·링크 모음뿐이거나, 문서 머리말·본문이 '이력', '폐기', '옛 설계'라고 표시한 "
        "내용뿐이면 usable을 false로 한다. 폐기된 내용으로 질문을 만들지 않는다.",
        "7. type은 "
        + " · ".join(PASSAGE_TYPES)
        + " 중 하나다. 설계 선택의 이유를 물으면 결정 이유, "
        "장애·실패 때 동작이면 장애 동작, 배포·인프라면 클라우드, 문서 작업 방식이면 팀 규칙.",
    ]
)
PASSAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "usable": {"type": "boolean"},
        "type": {"type": "string", "enum": list(PASSAGE_TYPES)},
        "question": {"type": "string"},
        "expected": {"type": "string"},
        "evidence_start": {"type": "integer"},
        "evidence_end": {"type": "integer"},
    },
    "required": ["usable", "type", "question", "expected", "evidence_start", "evidence_end"],
    "additionalProperties": False,
}

DECISION_SYSTEM = "\n".join(
    [
        "너는 북적북적(KTB4-13th) 팀 결정 로그로 검증용 질문을 만든다. 결정 하나마다 질문 하나를 "
        "만든다. 질문은 그 결정의 지금 값을 확인하려는 것이다.",
        "",
        "규칙",
        *[f"{n}. {rule}" for n, rule in enumerate(STYLE_RULES, 1)],
        '4. 결정 글을 그대로 옮기지 않는다. 예: "① 검색 키워드 매칭은 뭘로 해?"',
        "5. 입력의 line을 그대로 돌려준다.",
    ]
)
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"line": {"type": "integer"}, "question": {"type": "string"}},
                "required": ["line", "question"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}

UNKNOWN_SYSTEM = "\n".join(
    [
        "너는 북적북적(KTB4-13th) 팀 문서로 검증용 '모름' 질문을 만든다. "
        "팀원이 실제로 물을 법하지만 [문서 목록]의 문서들에는 답이 없을 질문이다"
        "(프론트엔드 세부, 외부 서비스 이름, 운영 절차, "
        "일정·담당자 등).",
        "",
        "규칙",
        *[f"{n}. {rule}" for n, rule in enumerate(STYLE_RULES, 1)],
        "4. absent_terms에는 답이 문서에 있다면 반드시 나올 구체적인 단어 2~6개를 쓴다"
        "(제품·라이브러리·회사 이름 등). 너무 흔한 단어(API, 서버, 배포)는 쓰지 않는다.",
        "5. 서로 다른 주제로 만든다.",
    ]
)
UNKNOWN_SCHEMA = {
    "type": "object",
    "properties": {
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "absent_terms": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["question", "absent_terms"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["questions"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Doc:
    path: str
    title: str
    lines: tuple[str, ...]
    body_start: int  # frontmatter 다음 줄 (1부터)


@dataclass(frozen=True)
class Window:
    doc: Doc
    start: int  # 1부터, 포함
    end: int

    def numbered(self) -> str:
        return "\n".join(
            f"{n:>5}  {self.doc.lines[n - 1]}" for n in range(self.start, self.end + 1)
        )


# ── 문서·구절 고르기 ──────────────────────────────────────────────


def load_docs(config: Config, root: Path, source: Source, skip: set[str]) -> list[Doc]:
    """포함 규칙으로 고른 문서 (skip 경로는 뺀다)."""
    directory = source_dir(config, root, source)
    docs = []
    for f in select_files(config, root, source):
        path = f.path
        if path in skip:
            continue
        lines = tuple(read_lines(directory / path))
        body_start, title = _frontmatter(lines)
        docs.append(Doc(path, title or _first_heading(lines) or path, lines, body_start))
    return docs


def _frontmatter(lines: Sequence[str]) -> tuple[int, str | None]:
    if not lines or lines[0].strip() != "---":
        return 1, None
    title = None
    for n, line in enumerate(lines[1:], 2):
        if line.strip() == "---":
            return n + 1, title
        key, _, value = line.partition(":")
        if key.strip() == "wiki":
            title = value.strip().strip("'\"") or None
    return 1, None


def _first_heading(lines: Sequence[str]) -> str | None:
    return next((m.group(2).strip() for line in lines if (m := HEADING.match(line))), None)


def pick_windows(
    docs: Sequence[Doc], rng: random.Random, avoid: dict[str, set[int]]
) -> list[Window]:
    """문서마다 크기에 비례해 겹치지 않는 창을 뽑는다. 옛 서술 줄(보정 파일)이 든 창은 뺀다."""
    windows = []
    for doc in docs:
        chars = sum(len(line) for line in doc.lines)
        wanted = max(1, min(MAX_PER_DOC, round(chars / CHARS_PER_QUESTION)))
        starts = [
            n
            for n in range(doc.body_start, len(doc.lines) + 1)
            if doc.lines[n - 1].strip()
            and (
                n == doc.body_start
                or not doc.lines[n - 2].strip()
                or HEADING.match(doc.lines[n - 1])
            )
        ]
        rng.shuffle(starts)
        taken: list[Window] = []
        for start in starts:
            if len(taken) == wanted:
                break
            end = min(len(doc.lines), start + WINDOW_LINES - 1)
            if any(start <= w.end and w.start <= end for w in taken):
                continue
            if any(start <= line <= end for line in avoid.get(doc.path, ())):
                continue
            taken.append(Window(doc, start, end))
        windows += sorted(taken, key=lambda w: w.start)
    return windows


def heading_path(doc: Doc, line: int) -> list[str]:
    """line 앞의 마크다운 헤딩 경로 (코드 블록 안 # 은 헤딩이 아니다)."""
    stack: list[tuple[int, str]] = []
    fenced = False
    for text in doc.lines[: line - 1]:
        if text.lstrip().startswith("```"):
            fenced = not fenced
            continue
        m = None if fenced else HEADING.match(text)
        if m:
            level = len(m.group(1))
            stack = [h for h in stack if h[0] < level] + [(level, m.group(2).strip())]
    return [title for _, title in stack]


# ── 만들기 ────────────────────────────────────────────────────────


def passage_prompt(window: Window) -> str:
    doc = window.doc
    head = [line for line in doc.lines[doc.body_start - 1 : doc.body_start + 14] if line.strip()]
    return "\n\n".join(
        [
            f"[문서] {doc.title} ({doc.path})",
            "[문서 머리말]\n" + "\n".join(head[:10]),
            f"[위치] {' > '.join(heading_path(doc, window.start)) or '(문서 첫머리)'}",
            "[구절] (왼쪽 숫자가 줄 번호)\n" + window.numbered(),
        ]
    )


def passage_item(window: Window, data: dict[str, Any]) -> dict[str, Any] | None:
    """LLM 답을 검사해 문항으로. 못 쓰면 None."""
    if data.get("usable") is not True:
        return None
    question = str(data.get("question", "")).strip()
    expected = str(data.get("expected", "")).strip()
    start, end = data.get("evidence_start"), data.get("evidence_end")
    if not question or not expected or not isinstance(start, int) or not isinstance(end, int):
        return None
    if not (window.start <= start <= end <= window.end) or end - start + 1 > MAX_EVIDENCE_LINES:
        return None
    if not any(window.doc.lines[n - 1].strip() for n in range(start, end + 1)):
        return None
    if data.get("type") not in PASSAGE_TYPES:
        return None
    return {
        "type": data["type"],
        "question": question,
        "expected": expected,
        "evidence": [{"path": window.doc.path, "lines": _span(start, end)}],
    }


def decision_prompt(table: DecisionTable) -> str:
    rows = [
        f"- line {d.line}: {d.date} {d.part} — {d.text}"
        for d in table.decisions
        if d.status == "현행"
    ]
    return "[결정]\n" + "\n".join(rows)


def decision_items(table: DecisionTable, data: dict[str, Any]) -> list[dict[str, Any]]:
    by_line = {d.line: d for d in table.decisions if d.status == "현행"}
    items, seen = [], set()
    for q in data.get("questions") or []:
        line, question = q.get("line"), str(q.get("question", "")).strip()
        if line not in by_line or line in seen or not question:
            continue
        seen.add(line)
        d = by_line[line]
        items.append(
            {
                # 문서 본문에 옛 서술이 남은 결정은 함정이다 (plan.md D15 ①)
                "type": "함정" if d.replaces or d.old_text_at else "현행 결정",
                "question": question,
                "expected": f"{d.text} (결정 로그 {d.date})",
                "evidence": [{"path": table.log_path, "lines": str(d.line)}],
            }
        )
    return items


def unknown_prompt(docs: Sequence[Doc]) -> str:
    listing = []
    for doc in docs:
        headings = [
            m.group(2).strip()
            for line in doc.lines
            if (m := HEADING.match(line)) and len(m.group(1)) == 2
        ]
        listing.append(f"- {doc.title}: {' / '.join(headings[:12])}")
    return "[문서 목록]\n" + "\n".join(listing) + f"\n\n질문 {UNKNOWN_QUESTIONS}개를 만든다."


def unknown_items(docs: Sequence[Doc], data: dict[str, Any]) -> list[dict[str, Any]]:
    """없음 확인 단어가 문서 어디에도 없는 질문만 남긴다."""
    text = "\n".join(line for doc in docs for line in doc.lines).lower()
    items = []
    for q in data.get("questions") or []:
        question = str(q.get("question", "")).strip()
        terms = [str(t).strip() for t in q.get("absent_terms") or [] if str(t).strip()]
        if not question or len(terms) < 2 or any(t.lower() in text for t in terms):
            continue
        items.append(
            {
                "type": "모름",
                "question": question,
                "expected": "모름. 문서에 없다",
                "evidence": [],
                "absent_terms": terms,
            }
        )
    return items


def drop_duplicates(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """같은 근거 줄이나 형태소가 거의 같은 질문은 먼저 나온 것만 남긴다."""
    kept: list[dict[str, Any]] = []
    tokens: list[set[str]] = []
    spans: set[tuple[str, str]] = set()
    for item in items:
        span = next(((e["path"], e["lines"]) for e in item["evidence"]), None)
        words = set(tokenize_kiwi(item["question"]))
        if span and span in spans:
            continue
        if any(_overlap(words, other) >= DUPLICATE_OVERLAP for other in tokens):
            continue
        kept.append(item)
        tokens.append(words)
        if span:
            spans.add(span)
    return kept


def assign_splits(items: list[dict[str, Any]], rng: random.Random) -> None:
    """유형마다 섞어 dev·test를 번갈아 붙인다."""
    by_type: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_type.setdefault(item["type"], []).append(item)
    for group in by_type.values():
        order = list(range(len(group)))
        rng.shuffle(order)
        for rank, index in enumerate(order):
            group[index]["split"] = "dev" if rank % 2 == 0 else "test"


def baseline_sample(items: list[dict[str, Any]], rng: random.Random, n: int) -> list[str]:
    """베이스라인에 물을 문항: dev·test에서 반씩."""
    picked = []
    for split in ("dev", "test"):
        ids = [i["id"] for i in items if i["split"] == split]
        picked += rng.sample(ids, min(len(ids), n // 2))
    return sorted(picked)


def generate(
    config: Config,
    root: Path,
    llm: OpenRouter,
    *,
    seed: int = SEED,
    progress: Callable[[str], None] = print,
) -> dict[str, Any]:
    assert config.decisions is not None
    source = next(s for s in config.sources if s.name == config.decisions.source)
    table = build_table(config, root)
    rng = random.Random(seed)
    avoid: dict[str, set[int]] = {}
    for d in table.decisions:
        for ref in d.old_text_at:
            path, _, line = ref.rpartition(":")
            if line.isdigit():
                avoid.setdefault(path, set()).add(int(line))
    all_docs = load_docs(config, root, source, skip=set())
    docs = [d for d in all_docs if d.path != table.log_path]  # 결정 로그는 결정 질문으로 따로
    windows = pick_windows(docs, rng, avoid)
    progress(f"문서 {len(docs)}개에서 구절 {len(windows)}개를 뽑았다")

    def ask_passage(window: Window) -> dict[str, Any] | None:
        result = llm.complete_json(PASSAGE_SYSTEM, passage_prompt(window), PASSAGE_SCHEMA, "item")
        return passage_item(window, result.data)

    passage, failed = [], 0
    with ThreadPoolExecutor(WORKERS) as pool:
        for item in pool.map(_tolerant(ask_passage), windows):
            if isinstance(item, LLMError):
                if item.fatal:
                    raise item
                failed += 1
            elif item is not None:
                passage.append(item)
    unusable = len(windows) - len(passage) - failed
    progress(f"구절 질문 {len(passage)}개 (못 쓴 구절 {unusable}, 오류 {failed})")

    decisions = decision_items(
        table, llm.complete_json(DECISION_SYSTEM, decision_prompt(table), DECISION_SCHEMA, "d").data
    )
    progress(f"결정 질문 {len(decisions)}개")
    unknown = unknown_items(
        all_docs,
        llm.complete_json(UNKNOWN_SYSTEM, unknown_prompt(docs), UNKNOWN_SCHEMA, "u").data,
    )
    progress(f"모름 질문 {len(unknown)}개 (없음 확인 단어가 문서에 있는 질문은 뺐다)")

    items = drop_duplicates(passage + decisions + unknown)
    for n, item in enumerate(items, 1):
        item["id"] = f"b{n:03d}"
    assign_splits(items, rng)
    commit = table.commit[:7]
    ordered = [
        {
            "id": item["id"],
            "type": item["type"],
            "version": "v0",
            "split": item["split"],
            "question": item["question"],
            "expected": item["expected"],
            "evidence": [
                {"repo": source.repo_name, "path": e["path"], "lines": e["lines"]}
                for e in item["evidence"]
            ],
            **({"absent_terms": item["absent_terms"]} if item.get("absent_terms") else {}),
            "status": "자동 생성",
        }
        for item in items
    ]
    return {
        "meta": {
            "checked_at": datetime.now().date().isoformat(),
            "source_commits": {source.repo_name: commit},
            "generated_by": llm.model,
            "seed": seed,
            "baseline_ids": baseline_sample(items, rng, BASELINE_SAMPLE),
            "note": "tka.bigset이 만든 검증셋. 정답은 레포 주인이 표본만 확인한다. "
            "dev는 틀린 이유를 보고 고쳐도 되고 test는 합계만 본다.",
        },
        "items": ordered,
    }


def _tolerant(fn):
    """한 구절이 실패해도 나머지는 계속한다. 오류는 값으로 돌려준다."""

    def call(arg):
        try:
            return fn(arg)
        except LLMError as e:
            return e

    return call


def _span(start: int, end: int) -> str:
    return str(start) if start == end else f"{start}-{end}"


def _overlap(a: set[str], b: set[str]) -> float:
    return len(a & b) / min(len(a), len(b)) if a and b else 0.0


# ── CLI ───────────────────────────────────────────────────────────


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tka.bigset", description="자동 생성 검증셋")
    parser.add_argument("command", choices=("generate", "review"))
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--out", type=Path, default=Path("eval/bigset.yaml"))
    parser.add_argument("--n", type=int, default=20, help="review: 표본 수")
    parser.add_argument("--config", type=Path, default=Path("config/ktb13.yaml"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)
    config = load_config(args.config)

    if args.command == "review":
        return _review(args, config)
    if (args.root / args.out).exists():
        print(f"멈춤 — {args.out}이 이미 있다. 다시 만들려면 지우고 돌린다", file=sys.stderr)
        return 1
    try:
        llm = OpenRouter(args.model, load_api_key(args.root / ".env"), temperature=TEMPERATURE)
        data = generate(config, args.root, llm)
    except LLMError as e:
        print(f"멈춤 — {e}", file=sys.stderr)
        return 1
    (args.root / args.out).write_text(dump_yaml(data), encoding="utf-8")
    items = data["items"]
    counts: dict[str, int] = {}
    for item in items:
        counts[item["type"]] = counts.get(item["type"], 0) + 1
    print(f"{args.out}: {len(items)}문항 — " + ", ".join(f"{k} {v}" for k, v in counts.items()))
    return 0


def _review(args, config: Config) -> int:
    golden = load_golden(args.root / args.out)
    rng = random.Random(SEED)
    sample = sorted(rng.sample([i.id for i in golden.items], min(args.n, len(golden.items))))
    picked = GoldenSet(golden.source_commits, tuple(i for i in golden.items if i.id in sample))
    out = args.root / "data" / "bigset_review.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_review(picked, config, args.root), encoding="utf-8")
    print(f"표본 {len(sample)}문항 확인 시트: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
