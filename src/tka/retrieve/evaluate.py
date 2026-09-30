"""검색 평가: 골든셋 질문으로 recall@5를 잰다. LLM 없이 검색만 본다 (plan.md 로드맵 5단계).

- 대상: v0 문항 중 근거가 있는 것 (모름 문항은 찾을 근거가 없어 뺀다). 근거 중 v0 범위 밖
  (outside_scope)은 빼고 본다.
- 적중: 상위 k개 청크 중 하나라도 근거 줄과 겹치면 적중. 근거가 여러 개인 문항(g16처럼 두 문서를
  함께 봐야 하는 것)은 모두 덮었는지(full)도 따로 센다.
- 비율과 개수를 같이 쓴다. 18문항이면 한 문항이 6%p다 (plan.md §4-1).
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tka.golden import Evidence, GoldenItem, GoldenSet
from tka.ingest.chunk import Chunk
from tka.retrieve.search import Hit, SearchIndex

K = 5
DEPTH = 10  # MRR과 "몇 위에 있었나"를 볼 깊이


@dataclass(frozen=True)
class Variant:
    name: str
    method: str  # keyword · vector · hybrid
    tokenizer: str = "kiwi"
    text_mode: str = "context"
    max_chars: int = 1500
    weights: tuple[float, float] = (1, 1)  # hybrid: (키워드, 벡터)


@dataclass(frozen=True)
class ItemResult:
    item_id: str
    item_type: str
    first_hit: int | None  # 근거와 겹친 첫 청크의 순위(1부터, DEPTH 안). 없으면 None
    covered: int  # 상위 K개가 덮은 근거 수
    total: int  # 근거 수
    top: tuple[str, ...]  # 상위 K개 청크 id


@dataclass(frozen=True)
class Summary:
    hits: int
    full: int
    n: int
    mrr: float

    def recall(self) -> str:
        return _ratio(self.hits, self.n)

    def full_recall(self) -> str:
        return _ratio(self.full, self.n)


def targets(golden: GoldenSet, version: str = "v0") -> list[tuple[GoldenItem, list[Evidence]]]:
    out = []
    for item in golden.for_version(version):
        evidence = [e for e in item.evidence if not e.outside_scope]
        if item.type != "모름" and evidence:
            out.append((item, evidence))
    return out


def overlaps(chunk: Chunk, ev: Evidence) -> bool:
    return (
        chunk.repo == ev.repo
        and chunk.path == ev.path
        and chunk.start_line <= ev.end
        and ev.start <= chunk.end_line
    )


def run(index: SearchIndex, variant: Variant, golden: GoldenSet) -> list[ItemResult]:
    search = searcher(index, variant)
    results = []
    for item, evidence in targets(golden):
        hits = search(item.question, DEPTH)
        first = next(
            (rank for rank, h in enumerate(hits, 1) if any(overlaps(h.chunk, e) for e in evidence)),
            None,
        )
        top = hits[:K]
        covered = sum(1 for e in evidence if any(overlaps(h.chunk, e) for h in top))
        results.append(
            ItemResult(
                item.id, item.type, first, covered, len(evidence), tuple(h.chunk.id for h in top)
            )
        )
    return results


def searcher(index: SearchIndex, variant: Variant) -> Callable[[str, int], list[Hit]]:
    if variant.method == "keyword":
        return index.keyword
    if variant.method == "vector":
        return index.vector
    if variant.method == "hybrid":
        return lambda q, k: index.hybrid(q, k, variant.weights)
    raise ValueError(f"method는 keyword·vector·hybrid 중 하나다: {variant.method!r}")


def summarize(results: list[ItemResult]) -> Summary:
    hits = sum(1 for r in results if r.first_hit is not None and r.first_hit <= K)
    full = sum(1 for r in results if r.covered == r.total)
    mrr = sum(1 / r.first_hit for r in results if r.first_hit) / len(results) if results else 0.0
    return Summary(hits, full, len(results), mrr)


def config_hash(*paths: Path) -> str:
    h = hashlib.sha256()
    for p in paths:
        h.update(p.read_bytes())
    return h.hexdigest()[:12]


def render_report(
    runs: list[tuple[Variant, list[ItemResult]]],
    *,
    meta: dict[str, str],
    titles: dict[str, str],
    best: str,
) -> str:
    lines = [f"# 검색 평가 recall@{K}", ""]
    lines += [f"- {k}: {v}" for k, v in meta.items()]
    lines += [
        "",
        "청크 크기를 따로 적지 않은 방식은 1500자(4단계에서 정한 값)다. 20문항 튜닝용 골든셋이라 "
        "한 문항이 6%p이고, 여기서 고른 방식은 따로 모은 채점용 질문으로 다시 재야 한다.",
        "",
        f"적중: 상위 {K}개 청크 중 하나라도 골든셋 근거 줄과 겹침. "
        "근거 전부: 근거가 여러 개인 문항(예: 결정 로그 + 명세)은 모두 덮음.",
        "",
        f"| 방식 | recall@{K} | 근거 전부 | MRR@{DEPTH} |",
        "|---|---|---|---|",
    ]
    for variant, results in runs:
        s = summarize(results)
        mark = " **(선택)**" if variant.name == best else ""
        lines.append(f"| {variant.name}{mark} | {s.recall()} | {s.full_recall()} | {s.mrr:.2f} |")

    chosen = next(r for v, r in runs if v.name == best)
    lines += [
        "",
        f"## 선택한 방식의 문항별 ({best})",
        "",
        "| id | 유형 | 첫 적중 순위 | 근거 | 1위 청크 |",
        "|---|---|---|---|---|",
    ]
    for r in chosen:
        rank = str(r.first_hit) if r.first_hit else f"{DEPTH}위 밖"
        top1 = titles.get(r.top[0], r.top[0]) if r.top else "-"
        lines.append(f"| {r.item_id} | {r.item_type} | {rank} | {r.covered}/{r.total} | {top1} |")

    types = sorted({r.item_type for r in chosen})
    lines += ["", "## 유형별 recall (선택한 방식)", "", "| 유형 | recall |", "|---|---|"]
    for t in types:
        rows = [r for r in chosen if r.item_type == t]
        hit = sum(1 for r in rows if r.first_hit and r.first_hit <= K)
        lines.append(f"| {t} | {_ratio(hit, len(rows))} |")
    return "\n".join(lines) + "\n"


def _ratio(num: int, den: int) -> str:
    return f"{num / den:.0%} ({num}/{den})" if den else "—"
