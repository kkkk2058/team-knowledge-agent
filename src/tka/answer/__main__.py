"""uv run python -m tka.answer eval --model M [--ids g01,g02]

골든셋 v0 문항에 답해 eval/results/<날짜>-bot-<모델>/answers.yaml에 남긴다. 기준 커밋 문서로
답하고 호출 로그는 남기지 않는다. 베이스라인과 같은 형식이라 python -m tka.evaluation summary로
채점·요약한다. 질문 하나에 답하는 사용자 명령은 `tka ask`다.
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from collections.abc import Sequence
from datetime import date, datetime
from pathlib import Path

from tka.answer.llm import DEFAULT_MODEL, LLMError, OpenRouter, load_api_key
from tka.answer.pipeline import SYSTEM_PROMPT, answer_question
from tka.config import load_config
from tka.decisions.table import DecisionError
from tka.evaluation import PROMPT_TEMPLATE, dump_yaml
from tka.golden import load_golden
from tka.index.vector import Embedder
from tka.ingest.fetch import FetchError
from tka.retrieve.search import DEFAULT_SETTINGS
from tka.service import KnowledgeService


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m tka.answer", description="골든셋 답변")
    parser.add_argument("command", choices=("eval",))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--ids", help="쉼표로 구분한 문항 id")
    parser.add_argument("--out", type=Path, help="결과 폴더")
    parser.add_argument("--config", type=Path, default=Path("config/ktb13.yaml"))
    parser.add_argument("--golden", type=Path, default=Path("eval/golden.yaml"))
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args(argv)

    config = load_config(args.config)
    try:
        llm = OpenRouter(args.model, load_api_key(args.root / ".env"))
        service = KnowledgeService(
            config, args.root, live=False, embedder=Embedder(args.root / config.index_path)
        )
        index, table, _ = service.search_index()
    except (LLMError, FetchError, DecisionError) as e:
        print(f"멈춤 — {e}", file=sys.stderr)
        return 1
    return _eval(args, config, index, table, llm)


def _eval(args, config, index, table, llm: OpenRouter) -> int:
    golden = load_golden(args.golden)
    wanted = set(args.ids.split(",")) if args.ids else None
    items = [i for i in golden.for_version("v0") if not wanted or i.id in wanted]
    slug = re.sub(r"[^a-z0-9.-]+", "-", args.model.lower()).strip("-")
    out = args.out or args.root / "eval" / "results" / f"{date.today().isoformat()}-bot-{slug}"
    out.mkdir(parents=True, exist_ok=True)

    answers = []
    for item in items:
        started = time.monotonic()
        try:
            a = answer_question(index, table, item.question, llm, aliases=config.aliases)
        except LLMError as e:
            answers.append({"id": item.id, "answer": "", "error": str(e)[:300]})
            print(f"{item.id}: 오류 — {e}")
            if e.fatal:
                print("다시 불러도 같은 이유로 실패하는 오류라 멈춘다", file=sys.stderr)
                _write(out, args, table, answers)
                return 1
            continue
        elapsed = round((time.monotonic() - started) * 1000)
        answers.append(
            {
                "id": item.id,
                "answer": a.render(),
                "meta": {
                    "duration_ms": elapsed,
                    "cost_usd": a.llm.cost_usd,
                    "input_tokens": a.llm.input_tokens,
                    "output_tokens": a.llm.output_tokens,
                    "unknown": a.unknown,
                    "dropped_sentences": len(a.dropped),
                    "sources": [s.citation for s in a.sources],
                },
            }
        )
        flag = " (모름)" if a.unknown else ""
        print(f"{item.id}: {elapsed / 1000:.1f}초{flag}, 뺀 문장 {len(a.dropped)}")

    _write(out, args, table, answers)
    return 0


def _write(out: Path, args, table, answers: list[dict]) -> None:
    data = {
        "run_id": out.name,
        "system": "bot",
        "detail": {
            "tool": "tka.answer",
            "models": [args.model],
            "search": DEFAULT_SETTINGS.label(),
            "prompt_template": PROMPT_TEMPLATE.format(question="{question}")
            + "\n\n(봇은 이 형식 대신 SYSTEM_PROMPT와 근거 블록을 쓴다)",
            "system_prompt": SYSTEM_PROMPT,
            "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        },
        "source_commits": {table.repo: table.commit[:7]},
        "answers": answers,
    }
    (out / "answers.yaml").write_text(dump_yaml(data), encoding="utf-8")
    print(f"답: {out}/answers.yaml")


if __name__ == "__main__":
    sys.exit(main())
