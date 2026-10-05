"""설정의 소스에서 API 대조표를 만들고 마크다운으로 그린다 (implementation.md ⑨)."""

from __future__ import annotations

from dataclasses import replace
from fnmatch import fnmatch
from pathlib import Path

from tka.config import CallerContract, Config, ContractsConfig, ServiceContract
from tka.contracts.compare import Report, compare_calls, compare_spec, uncalled
from tka.contracts.parse import (
    Endpoint,
    fastapi_routes,
    fetch_calls,
    restclient_calls,
    spec_endpoints,
    spring_routes,
)
from tka.ingest.fetch import FetchError, git, source_dir
from tka.ingest.files import select_files


def build_report(config: Config, root: Path) -> Report:
    """받아 둔 소스(기준 커밋)를 읽는다. 기준 커밋이 아니면 FetchError."""
    contracts = _contracts(config)
    servers = {s.name: _server_endpoints(config, root, contracts, s) for s in contracts.services}
    callers = tuple(
        compare_calls(c.name, c.target, *_caller_endpoints(config, root, contracts, c, servers))
        for c in contracts.callers
    )
    services = []
    for s in contracts.services:
        spec_source = contracts.source(s.spec_source)
        spec_file = source_dir(config, root, spec_source) / s.spec_path
        if not spec_file.is_file():
            raise FetchError(f"{spec_source.repo_name}: 명세 파일이 없다: {s.spec_path}")
        where = f"{spec_source.repo_name}/{s.spec_path}"
        spec = spec_endpoints(spec_file.read_text(encoding="utf-8"), where)
        report = compare_spec(s.name, where, spec, servers[s.name])
        mine = [c for c in callers if c.target == s.name]
        # 부르는 쪽을 하나도 설정하지 않은 서비스는 "아무도 안 부름"을 셀 수 없다
        services.append(
            replace(report, uncalled=uncalled(servers[s.name], mine)) if mine else report
        )
    commits = {
        src.repo_name: git(source_dir(config, root, src), "rev-parse", "--short=7", "HEAD").strip()
        for src in contracts.sources
    }
    return Report(commits, tuple(services), callers)


def _contracts(config: Config) -> ContractsConfig:
    if config.contracts is None:
        raise FetchError("설정에 contracts가 없다 (config/ktb13.yaml)")
    return config.contracts


def _read(config: Config, root: Path, contracts: ContractsConfig, name: str, patterns=()):
    source = contracts.source(name)
    directory = source_dir(config, root, source)
    files = select_files(config, root, source)  # 기준 커밋인지도 여기서 검사한다
    return source.repo_name, {
        f.path: (directory / f.path).read_text(encoding="utf-8")
        for f in files
        if not patterns or any(fnmatch(f.path, p) for p in patterns)
    }


def _server_endpoints(config, root, contracts, service: ServiceContract) -> list[Endpoint]:
    repo, files = _read(config, root, contracts, service.server_source)
    parse = fastapi_routes if service.server_kind == "fastapi" else spring_routes
    return parse(files, repo)


def _caller_endpoints(config, root, contracts, caller: CallerContract, servers):
    repo, files = _read(config, root, contracts, caller.source, caller.paths)
    if caller.kind == "restclient":
        calls, unresolved = restclient_calls(files, repo)
    else:
        calls = fetch_calls(files, repo, prefix=caller.path_prefix, functions=caller.functions)
        unresolved = []
    return calls, servers[caller.target], unresolved


# ── 마크다운 ──────────────────────────────────────────────────────


def render_markdown(report: Report, generated_on: str) -> str:
    commits = " · ".join(f"{repo} `{c}`" for repo, c in report.commits.items())
    lines = [
        "# API 대조표",
        "",
        f"> {generated_on} · {commits} · `uv run python -m tka.contracts check`",
        "> LLM 없이 코드·명세를 정규식으로 읽었다. 의도된 차이인지는 사람이 판단한다.",
        "",
        "## 요약",
        "",
        "| 명세 ↔ 서버 | 명세 | 서버 코드 | 일치 | 메서드 다름 | 명세에만 | 코드에만 |",
        "|---|---|---|---|---|---|---|",
    ]
    for s in report.services:
        spec_n = len(s.matched) + len(s.method_differs) + len(s.spec_only)
        code_n = len(s.matched) + len(s.method_differs) + len(s.code_only)
        lines.append(
            f"| {s.name} | {spec_n} | {code_n} | {len(s.matched)} | {len(s.method_differs)} "
            f"| {len(s.spec_only)} | {len(s.code_only)} |"
        )
    if report.callers:
        lines += [
            "",
            "| 호출 → 서버 | 호출 | 서버에 있음 | 메서드 다름 | **서버에 없음** | 경로 못 읽음 |",
            "|---|---|---|---|---|---|",
        ]
        for c in report.callers:
            total = len(c.matched) + len(c.method_differs) + len(c.missing)
            lines.append(
                f"| {c.name} | {total} | {len(c.matched)} | {len(c.method_differs)} "
                f"| {len(c.missing)} | {len(c.unresolved)} |"
            )

    for c in report.callers:
        lines += ["", f"## {c.name} 호출"]
        lines += _section(
            "서버에 없는 경로를 부른다",
            ["호출", "위치"],
            [(f"`{e.label()}`", e.where) for e in c.missing],
        )
        lines += _section(
            "메서드가 다르다",
            ["호출", "서버", "호출 위치", "서버 위치"],
            [
                (f"`{p.left.label()}`", f"`{p.right.label()}`", p.left.where, p.right.where)
                for p in c.method_differs
            ],
        )
        lines += _section("경로를 못 읽은 호출", ["위치"], [(w,) for w in c.unresolved])

    for s in report.services:
        lines += ["", f"## {s.name}: 명세 ↔ 서버 코드", "", f"명세: `{s.spec_where}`"]
        lines += _section(
            "메서드가 다르다",
            ["경로", "명세", "코드", "명세 위치", "코드 위치"],
            [
                (
                    f"`{p.left.path}`",
                    p.left.method,
                    p.right.method or "?",
                    p.left.where,
                    p.right.where,
                )
                for p in s.method_differs
            ],
        )
        lines += _section(
            "명세에만 있다 (코드에 없음)",
            ["명세", "설명", "비슷한 코드 경로"],
            [(f"`{e.label()}`", e.note, f"`{h.label()}`" if h else "") for e, h in s.spec_only],
        )
        lines += _section(
            "코드에만 있다 (명세에 없음)",
            ["코드", "위치"],
            [(f"`{e.label()}`", e.where) for e in s.code_only],
        )
        lines += _section(
            "아무 호출 쪽도 부르지 않는다 (참고: 외부·내부 전용일 수 있다)",
            ["코드", "위치"],
            [(f"`{e.label()}`", e.where) for e in s.uncalled],
        )
        lines += _section(
            "일치",
            ["엔드포인트", "명세 위치", "코드 위치"],
            [(f"`{p.right.label()}`", p.left.where, p.right.where) for p in s.matched],
        )
    return "\n".join(lines) + "\n"


def _section(title: str, header: list[str], rows: list[tuple]) -> list[str]:
    out = ["", f"### {title} · {len(rows)}"]
    if not rows:
        return out + ["", "없음"]
    out += ["", "| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(str(x).replace("|", "\\|") for x in row) + " |" for row in rows]
    return out
