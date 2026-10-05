"""LLM 채점: 실행 결과(answers.yaml)를 골든셋 기준 정답·근거 원문과 비교해 scores.yaml을 만든다.

    uv run python -m tka.judge score <run> [--golden G] [--out scores.yaml] [--model M]
    uv run python -m tka.judge agree <run> --llm scores-llm.yaml   사람 채점(scores.yaml)과 일치율

자동 생성 검증셋은 수백 문항이라 사람이 다 채점할 수 없다(plan.md D18).

- 채점 모델은 봇(Gemini)·베이스라인(Claude)과 다른 계열이다.
- 기준은 사람 채점과 같다: 정답 1 · 부분 0.5 · 오답 0. 함정은 지금 값을 맞혔는지, 모름 문항은
  지어내지 않았는지로 본다(plan.md D15). 인용 형식·길이는 보지 않는다.
- 믿을 만한지는 사람 채점이 있는 실행(골든셋 20문항 × 봇·베이스라인)에서 일치율로 잰다.
- 문항 자체가 틀렸으면(정답이 근거와 안 맞음, 질문이 모호함) item_problem으로 표시하고
  정답률에서 뺀다. 레포 주인이 그 문항을 확인한다.
- 틀린 답 유형은 코드가 정한다. 모름 문항을 틀리면 지어냄. 나머지는 골든셋 근거 줄이 시스템이
  본 근거(봇은 LLM에 넘긴 조각, 베이스라인은 인용)에 있었으면 근거 찾고 틀림, 없었으면 근거 못 찾음.
- 사람 채점 파일을 덮어쓰지 않는다.
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path
from typing import Any

from tka.answer.llm import LLMError, OpenRouter, load_api_key
from tka.config import load_config
from tka.evaluation import (
    VERDICTS,
    Answer,
    SourceFiles,
    check_citations,
    dump_yaml,
    extract_citations,
    load_run,
    load_scores,
    load_sources,
)
from tka.golden import GoldenItem, load_golden
from tka.ingest.files import read_lines

MODEL = "openai/gpt-4o-mini"  # 실험 비용 한도 안에서 고른 다른 계열 모델 (2026-10-06)
TEMPERATURE = 0
WORKERS = 8
CONTEXT_LINES = 2  # 근거 줄 앞뒤로 더 보여 줄 줄 수

SYSTEM_PROMPT = "\n".join(
    [
        "너는 팀 문서 질문 답변을 채점한다. "
        "[질문], [기준 정답], [근거 원문], [답]을 보고 판정한다.",
        "",
        "판정",
        "- 핵심은 기준 정답 중 질문이 직접 묻는 값·이유다. 질문이 묻지 않은 부가 설명(예외 조건, "
        "세부 사항)은 핵심이 아니다.",
        "- 정답: 핵심을 맞게 말했다. 부가 설명이 빠져도, 표현이 달라도, 틀린 내용 없이 더 말해도 "
        "정답이다.",
        "- 부분: 핵심 중 일부만 맞다(예: 두 가지를 물었는데 하나만, 이유 여러 개 중 일부만). "
        "또는 맞는 답과 함께 핵심과 어긋나는 틀린 주장을 했다.",
        "- 오답: 핵심이 틀렸거나 없다. 근거가 있는데 모른다고 했다. 옛 값을 지금 값처럼 답했다.",
        '- 기준 정답이 "모름"이면: 문서에 없다·모른다고 하고 값을 지어내지 않으면 정답, '
        "그럴듯한 값을 단정하면 오답이다.",
        "- 바뀐 이력·이전 결정에 대한 덧붙임은 맞든 틀리든 채점하지 않는다.",
        "- [채점 메모]가 있으면 따른다(예: 둘 다 말해야 정답, 범위를 밝히지 않으면 오답).",
        "- 인용 형식, 답 길이, 말투는 보지 않는다.",
        "",
        "item_problem: 기준 정답이 근거 원문과 맞지 않거나, 질문이 모호해 여러 답이 맞을 수 있으면 "
        "true. 그 문항은 점수에서 뺀다. 아니면 false.",
        "reason: 판정 이유를 한국어 한두 문장으로.",
    ]
)
SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": list(VERDICTS)},
        "item_problem": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["verdict", "item_problem", "reason"],
    "additionalProperties": False,
}


def build_prompt(item: GoldenItem, answer: Answer, sources: dict[str, SourceFiles]) -> str:
    blocks = [f"[질문]\n{item.question}", f"[기준 정답]\n{item.expected}"]
    notes = [x for x in (item.trap, item.note) if x]
    if notes:
        blocks.append("[채점 메모]\n" + "\n".join(notes))
    if item.absent_terms:
        blocks.append(f"[문서에 없는 것으로 확인한 단어]\n{', '.join(item.absent_terms)}")
    evidence = []
    for ev in item.evidence:
        source = sources.get(ev.repo)
        path = source.directory / ev.path if source else None
        if path is None or not path.is_file():
            continue
        lines = read_lines(path)
        start, end = max(1, ev.start - CONTEXT_LINES), min(len(lines), ev.end + CONTEXT_LINES)
        scope = " (봇의 검색 범위 밖)" if ev.outside_scope else ""
        evidence.append(
            f"{ev.label()}{scope}\n"
            + "\n".join(f"{n:>5}  {lines[n - 1]}" for n in range(start, end + 1))
        )
    blocks.append("[근거 원문]\n" + ("\n\n".join(evidence) or "(없음)"))
    blocks.append(f"[답]\n{answer.answer}")
    return "\n\n".join(blocks)


def failure_type(
    item: GoldenItem, answer: Answer, verdict: str, sources: dict[str, SourceFiles]
) -> str | None:
    if verdict == "정답":
        return None
    if item.type == "모름":
        return "지어냄"
    seen = "\n".join(map(str, answer.meta.get("sources") or [])) + "\n" + answer.answer
    found = any(c.hits_evidence for c in check_citations(extract_citations(seen), item, sources))
    return "근거 찾고 틀림" if found else "근거 못 찾음"


def score_run(
    items: Sequence[GoldenItem],
    answers: dict[str, Answer],
    sources: dict[str, SourceFiles],
    llm: OpenRouter,
) -> tuple[list[dict[str, Any]], list[str]]:
    """(채점 결과, 채점하지 못한 문항과 이유). 다시 불러도 같은 오류(fatal)면 멈춘다."""
    todo = [(i, answers[i.id]) for i in items if i.id in answers and not answers[i.id].error]

    def judge(pair: tuple[GoldenItem, Answer]) -> dict[str, Any] | LLMError:
        item, answer = pair
        try:
            result = llm.complete_json(
                SYSTEM_PROMPT, build_prompt(item, answer, sources), SCHEMA, "score"
            )
        except LLMError as e:
            return e
        data = result.data
        verdict = data.get("verdict")
        if verdict not in VERDICTS:
            return LLMError(f"{item.id}: 판정이 허용값이 아니다: {verdict!r}")
        record: dict[str, Any] = {"id": item.id, "verdict": verdict}
        failure = failure_type(item, answer, verdict, sources)
        if failure:
            record["failure"] = failure
        if data.get("item_problem") is True:
            record["item_problem"] = True
        record["reason"] = str(data.get("reason", "")).strip()
        return record

    scored, skipped = [], []
    with ThreadPoolExecutor(WORKERS) as pool:
        for (item, _), result in zip(todo, pool.map(judge, todo), strict=True):
            if isinstance(result, LLMError):
                if result.fatal:
                    raise result
                skipped.append(f"{item.id}: {result}")
            else:
                scored.append(result)
    return scored, skipped


def agreement(human: dict, llm: dict) -> tuple[int, int, list[str]]:
    """(같은 판정 수, 둘 다 채점한 수, 다른 문항 설명)."""
    both = sorted(set(human) & set(llm))
    differ = [
        f"{i}: 사람 {human[i].verdict} · LLM {llm[i].verdict} — {llm[i].reason or ''}"
        for i in both
        if human[i].verdict != llm[i].verdict
    ]
    return len(both) - len(differ), len(both), differ


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tka.judge", description="LLM 채점")
    parser.add_argument("command", choices=("score", "agree"))
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--golden", type=Path, default=Path("eval/golden.yaml"))
    parser.add_argument("--out", default="scores.yaml", help="score: 실행 폴더 안 파일 이름")
    parser.add_argument("--llm", default="scores-llm.yaml", help="agree: LLM 채점 파일 이름")
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--config", type=Path, default=Path("config/ktb13.yaml"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    if args.command == "agree":
        human = load_scores(args.run_dir)
        llm = load_scores(args.run_dir, args.llm)
        same, total, differ = agreement(human, llm)
        print(f"사람·LLM 판정 일치 {same}/{total}")
        for line in differ:
            print(f"  {line}")
        return 0

    out = args.run_dir / args.out
    if out.exists():
        print(f"멈춤 — {out}이 이미 있다. 사람 채점일 수 있어 덮어쓰지 않는다", file=sys.stderr)
        return 1
    golden = load_golden(args.golden)
    run = load_run(args.run_dir)
    try:
        llm = OpenRouter(args.model, load_api_key(args.root / ".env"), temperature=TEMPERATURE)
        sources = load_sources(golden, load_config(args.config), args.root)
        scored, skipped = score_run(golden.for_version("v0"), run.answers, sources, llm)
    except LLMError as e:
        print(f"멈춤 — {e}", file=sys.stderr)
        return 1
    data = {
        "scored_by": f"LLM 채점 ({args.model}, {date.today().isoformat()}, tka.judge)",
        "rule": "tka.judge SYSTEM_PROMPT (사람 채점과 같은 기준). item_problem 문항은 뺀다",
        "items": scored,
    }
    out.write_text(dump_yaml(data), encoding="utf-8")
    verdicts = Counter(s["verdict"] for s in scored)
    problems = sum(1 for s in scored if s.get("item_problem"))
    print(
        f"{out}: {len(scored)}문항 — "
        + ", ".join(f"{v} {verdicts[v]}" for v in VERDICTS)
        + f", 문항 오류 {problems}"
    )
    for line in skipped:
        print(f"  채점 못 함 {line}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
