"""API 대조표: 명세 ↔ 서버 코드, 호출 코드 ↔ 서버 코드를 맞춘다 (implementation.md ⑨).

같은 엔드포인트는 메서드와 정규화한 경로(parse.normalize_path)가 같은 것이다.
경로는 같은데 메서드만 다르면 "메서드 다름"으로 따로 센다. 메서드를 모르는 쪽(None)은
경로만 맞으면 맞은 것으로 본다.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from tka.contracts.parse import Endpoint

SIMILAR_RATIO = 0.6  # 명세에만 있는 경로에 "비슷한 코드 경로"를 붙이는 기준


@dataclass(frozen=True)
class Pair:
    left: Endpoint  # 명세 또는 호출
    right: Endpoint  # 서버 코드


@dataclass(frozen=True)
class ServiceReport:
    name: str
    spec_where: str
    matched: tuple[Pair, ...]
    method_differs: tuple[Pair, ...]
    spec_only: tuple[tuple[Endpoint, Endpoint | None], ...]  # (명세, 비슷한 코드 경로)
    code_only: tuple[Endpoint, ...]
    uncalled: tuple[Endpoint, ...] = ()  # 서버에 있는데 설정한 호출 쪽 누구도 안 부름


@dataclass(frozen=True)
class CallerReport:
    name: str
    target: str
    matched: tuple[Pair, ...]
    method_differs: tuple[Pair, ...]
    missing: tuple[Endpoint, ...]  # 서버에 없는 경로를 부른다
    unresolved: tuple[str, ...] = ()  # 경로를 못 읽은 호출 위치


@dataclass(frozen=True)
class Report:
    commits: dict[str, str]  # 레포 이름 → 커밋
    services: tuple[ServiceReport, ...]
    callers: tuple[CallerReport, ...] = field(default=())


def compare_spec(name: str, spec_where: str, spec: Sequence[Endpoint], code: Sequence[Endpoint]):
    """먼저 메서드까지 맞는 쌍을 다 고른 뒤, 남은 것끼리 경로만 맞는 쌍(메서드 다름)을 고른다.

    순서대로 한 번에 고르면 PATCH 명세가 DELETE 코드와 짝지어지는 식으로 엉뚱한 쌍이 생긴다.
    """
    matched, differs, spec_only = [], [], []
    used: set[int] = set()
    rest = []
    for s in spec:
        i = _pick(code, used, lambda c, s=s: c.key == s.key and _same_method(s, c))
        if i is None:
            rest.append(s)
        else:
            matched.append(Pair(s, code[i]))
            used.add(i)
    for s in rest:
        i = _pick(code, used, lambda c, s=s: c.key == s.key)
        if i is None:
            spec_only.append(s)
        else:
            differs.append(Pair(s, code[i]))
            used.add(i)
    code_only = [c for i, c in enumerate(code) if i not in used]
    hinted = tuple((s, _similar(s, code_only)) for s in spec_only)
    return ServiceReport(name, spec_where, tuple(matched), tuple(differs), hinted, tuple(code_only))


def compare_calls(
    name: str, target: str, calls: Sequence[Endpoint], server: Sequence[Endpoint], unresolved=()
):
    """호출 경로는 서버의 경로 변수 자리에 실제 값이 들어간다(`/auth/kakao/login`).
    그래서 경로가 똑같은 서버 엔드포인트를 먼저 찾고, 없으면 변수 자리를 아무 값으로 본다."""
    matched, differs, missing = [], [], []
    for call in calls:
        same_path = [c for c in server if c.key == call.key] or [
            c for c in server if path_fits(call.key, c.key)
        ]
        exact = [c for c in same_path if _same_method(call, c)]
        if exact:
            matched.append(Pair(call, exact[0]))
        elif same_path:
            differs.append(Pair(call, same_path[0]))
        else:
            missing.append(call)
    return CallerReport(
        name, target, tuple(matched), tuple(differs), tuple(missing), tuple(unresolved)
    )


def path_fits(call: str, server: str) -> bool:
    """정규화한 경로끼리, 조각 수가 같고 조각마다 같거나 한쪽이 변수(`{}`)다."""
    a, b = call.split("/"), server.split("/")
    return len(a) == len(b) and all(x == y or "{}" in (x, y) for x, y in zip(a, b, strict=True))


def uncalled(server: Sequence[Endpoint], callers: Sequence[CallerReport]) -> tuple[Endpoint, ...]:
    """설정한 호출 쪽 누구도 부르지 않는 서버 엔드포인트. 외부·내부 전용일 수 있어 참고용이다.

    메서드를 모르는 호출은 같은 경로의 서버 엔드포인트를 모두 부를 수 있는 것으로 본다.
    """
    hit = set()
    for c in callers:
        for p in c.matched:
            if p.left.method is None:
                hit.update((e.method, e.key) for e in server if e.key == p.right.key)
            else:
                hit.add((p.right.method, p.right.key))
    return tuple(e for e in server if (e.method, e.key) not in hit)


def _pick(code: Sequence[Endpoint], used: set[int], ok) -> int | None:
    return next((i for i, c in enumerate(code) if i not in used and ok(c)), None)


def _same_method(a: Endpoint, b: Endpoint) -> bool:
    return a.method is None or b.method is None or a.method == b.method


def _similar(spec: Endpoint, candidates: Sequence[Endpoint]) -> Endpoint | None:
    """메서드가 같고, 두 경로가 함께 가진 앞 조각(`/api/v1` 등)을 뺀 나머지가 비슷한 코드 경로.

    앞 조각까지 넣고 비교하면 모든 경로가 `/api/v1/`을 공유해 엉뚱한 경로가 비슷해 보인다.
    """
    best, best_ratio = None, SIMILAR_RATIO
    for c in candidates:
        if c.method is not None and c.method != spec.method:
            continue
        a, b = _drop_common_head(spec.key, c.key)
        ratio = SequenceMatcher(None, a, b).ratio()
        if ratio >= best_ratio:
            best, best_ratio = c, ratio
    return best


def _drop_common_head(a: str, b: str) -> tuple[str, str]:
    xs, ys = a.strip("/").split("/"), b.strip("/").split("/")
    n = 0
    while n < min(len(xs), len(ys)) and xs[n] == ys[n]:
        n += 1
    return "/".join(xs[n:]), "/".join(ys[n:])
