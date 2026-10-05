"""대조표에서 API 하나를 찾는다: 명세·서버 코드·그걸 부르는 호출을 한 묶음으로 (MCP check_api).

묶음(ApiEntry) 하나는 서비스의 엔드포인트 하나다. 서버에 없는 경로를 부르는 호출은 따로 묶음이 된다.
"""

from __future__ import annotations

from dataclasses import dataclass

from tka.contracts.compare import Report, path_fits
from tka.contracts.parse import Endpoint, normalize_path

# 어긋남이 심한 순서. 서버에 없는 경로를 부르면 실행하자마자 404가 난다.
STATUSES = ("서버에 없음", "메서드 다름", "명세에만", "코드에만", "일치")


@dataclass(frozen=True)
class Call:
    caller: str  # 예: FE→BE
    endpoint: Endpoint
    ok: bool  # 서버 엔드포인트와 메서드까지 맞나


@dataclass(frozen=True)
class ApiEntry:
    service: str
    status: str  # STATUSES
    spec: Endpoint | None
    code: Endpoint | None
    calls: tuple[Call, ...]
    spec_where: str  # 명세에 없을 때 어느 문서를 봤는지 알린다
    calls_checked: bool  # 이 서비스를 부르는 쪽을 설정했나 (아니면 "안 부름"을 말할 수 없다)
    hint: Endpoint | None = None  # 명세에만 있을 때 비슷한 코드 경로

    @property
    def main(self) -> Endpoint:
        """제목에 쓸 엔드포인트: 코드, 없으면 명세, 없으면 호출."""
        return self.code or self.spec or self.calls[0].endpoint

    def endpoints(self) -> list[Endpoint]:
        return [e for e in (self.spec, self.code) if e] + [c.endpoint for c in self.calls]

    def has_method(self, method: str) -> bool:
        """명세·코드의 메서드로 본다. 둘 다 없을 때(서버에 없는 호출)만 호출의 메서드로 본다."""
        own = [e for e in (self.spec, self.code) if e] or [c.endpoint for c in self.calls]
        return any(e.method in (None, method) for e in own)


def api_entries(report: Report) -> list[ApiEntry]:
    """메서드를 모르는 호출(래퍼 함수에 경로만 넘김)은 같은 경로의 서버 엔드포인트 모두에 붙인다.
    대조표가 짝지은 하나(처음 맞은 것)에만 붙이면 DELETE 래퍼가 PUT 아래에 나온다."""
    called = {c.target for c in report.callers}
    codes = {
        s.name: [p.right for p in (*s.matched, *s.method_differs)] + list(s.code_only)
        for s in report.services
    }
    calls_by_code: dict[Endpoint, list[Call]] = {}
    for c in report.callers:
        for pair, ok in [(p, True) for p in c.matched] + [(p, False) for p in c.method_differs]:
            if pair.left.method is None:
                targets = [e for e in codes.get(c.target, []) if e.key == pair.right.key]
            else:
                targets = [pair.right]
            for code in targets:
                calls_by_code.setdefault(code, []).append(Call(c.name, pair.left, ok))

    entries = []
    for s in report.services:

        def entry(status, spec, code, hint=None, s=s):
            return ApiEntry(
                s.name,
                status,
                spec,
                code,
                tuple(calls_by_code.get(code, ())) if code else (),
                s.spec_where,
                s.name in called,
                hint,
            )

        entries += [entry("일치", p.left, p.right) for p in s.matched]
        entries += [entry("메서드 다름", p.left, p.right) for p in s.method_differs]
        entries += [entry("명세에만", e, None, hint) for e, hint in s.spec_only]
        entries += [entry("코드에만", None, e) for e in s.code_only]
    for c in report.callers:
        spec_where = next((s.spec_where for s in report.services if s.name == c.target), "")
        entries += [
            ApiEntry(
                c.target, "서버에 없음", None, None, (Call(c.name, e, False),), spec_where, True
            )
            for e in c.missing
        ]
    return entries


def find_api(entries: list[ApiEntry], query: str, method: str = "") -> list[ApiEntry]:
    """query가 `/`로 시작하면 경로로(변수 자리는 아무 값, 그 아래 경로 포함), 아니면 경로·명세
    설명에 들어 있는 낱말로 찾는다. 비우면 전부. method가 있으면 그 메서드(모르는 쪽 포함)만."""
    q = query.strip()
    wanted = method.strip().upper()
    found = []
    for entry in entries:
        if wanted and not entry.has_method(wanted):
            continue
        if q and not any(_matches(e, q) for e in entry.endpoints()):
            continue
        found.append(entry)
    return sorted(found, key=lambda e: STATUSES.index(e.status))


def _matches(e: Endpoint, q: str) -> bool:
    if q.startswith("/"):
        nq = normalize_path(q)
        return e.key == nq or path_fits(nq, e.key) or e.key.startswith(nq.rstrip("/") + "/")
    lowered = q.lower()
    return lowered in e.key.lower() or lowered in e.note.lower()
