"""팀 설정 파일을 읽는다. 팀 하나 = 설정 파일 하나 (plan.md D7)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

# 평가 기준을 고정하려면 브랜치 이름이 아니라 커밋을 적어야 한다 (implementation.md ①).
COMMIT_PATTERN = re.compile(r"[0-9a-f]{7,40}")
REPO_PATTERN = re.compile(r"[\w.-]+/[\w.-]+")


class ConfigError(ValueError):
    """설정 파일의 형식이나 값이 잘못됐다."""


@dataclass(frozen=True)
class Source:
    name: str
    repo: str  # owner/name
    ref: str  # 고정 커밋
    include: tuple[str, ...]
    exclude: tuple[str, ...]
    extensions: tuple[str, ...]

    @property
    def repo_name(self) -> str:
        """owner를 뺀 레포 이름. 예: KTB4-13th-wiki"""
        return self.repo.split("/", 1)[1]

    def includes(self, path: str) -> bool:
        """레포 루트 기준 경로(`/` 구분)가 포함 규칙에 드는가.

        include·exclude 항목이 `/`로 끝나면 폴더, 아니면 파일 하나를 뜻한다.
        """
        return (
            path.endswith(self.extensions)
            and any(matches_rule(path, rule) for rule in self.include)
            and not any(matches_rule(path, rule) for rule in self.exclude)
        )


def matches_rule(path: str, rule: str) -> bool:
    """`/`로 끝나는 규칙은 폴더, 아니면 파일 하나."""
    return path.startswith(rule) if rule.endswith("/") else path == rule


@dataclass(frozen=True)
class DecisionsConfig:
    source: str  # 결정 로그가 있는 소스 이름
    log: str  # 그 소스 레포 루트 기준 경로
    corrections: Path | None  # 번복 관계 보정 파일, 이 레포 루트 기준


SERVER_KINDS = ("fastapi", "spring")
CALLER_KINDS = ("restclient", "fetch")


@dataclass(frozen=True)
class ServiceContract:
    """API를 여는 쪽 하나: 명세 문서와 서버 코드."""

    name: str  # 예: AI
    spec_source: str  # contracts.sources의 이름
    spec_path: str  # 그 소스 레포 루트 기준
    server_source: str
    server_kind: str  # SERVER_KINDS


@dataclass(frozen=True)
class CallerContract:
    """API를 부르는 쪽 하나."""

    name: str  # 예: BE→AI
    target: str  # 부르는 서비스 이름 (ServiceContract.name)
    source: str  # contracts.sources의 이름
    kind: str  # CALLER_KINDS
    paths: tuple[str, ...]  # 소스 파일 중 이 패턴(fnmatch)에 맞는 것만. 비면 전부
    path_prefix: str = "/"  # fetch: 이 문자열로 시작하는 문자열만 API 경로로 본다
    # fetch: 첫 인자가 경로이고 method를 안 적으면 GET인 함수. 그 밖의 함수는 메서드를 모른다
    functions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContractsConfig:
    """API 대조표 (implementation.md ⑨). 소스는 문서 색인과 따로 받는다."""

    sources: tuple[Source, ...]
    services: tuple[ServiceContract, ...]
    callers: tuple[CallerContract, ...]

    def source(self, name: str) -> Source:
        return next(s for s in self.sources if s.name == name)


@dataclass(frozen=True)
class Config:
    team: str
    decision_log_year: int
    cache_dir: Path  # 레포 루트 기준
    index_path: Path  # 레포 루트 기준
    sources: tuple[Source, ...]
    decisions: DecisionsConfig | None = None
    # 같은 것을 가리키는 말 묶음. 예: ("④", "feed", "피드"). 결정 찾기에서 서로 바꿔 찾는다.
    aliases: tuple[tuple[str, ...], ...] = ()
    contracts: ContractsConfig | None = None

    def source_for_repo(self, repo_name: str) -> Source | None:
        """레포 이름(owner 제외)으로 소스를 찾는다. 설정에 없으면 None."""
        return next((s for s in self.sources if s.repo_name == repo_name), None)


def load_config(path: Path) -> Config:
    """설정 파일을 읽어 검증한다. 파일이 없으면 FileNotFoundError를 그대로 올린다."""
    text = path.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise ConfigError(f"{path}: YAML 문법 오류: {e}") from e
    if not isinstance(raw, dict):
        raise ConfigError(f"{path}: 최상위가 매핑이 아니다")

    paths = _get(raw, "paths", dict, "")
    raw_sources = _get(raw, "sources", list, "")
    if not raw_sources:
        raise ConfigError("sources: 소스가 하나도 없다")

    sources = tuple(_parse_source(s, f"sources[{i}]") for i, s in enumerate(raw_sources))
    names = [s.name for s in sources]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ConfigError(f"sources: 이름이 겹친다: {', '.join(duplicates)}")

    return Config(
        team=_get(raw, "team", str, ""),
        decision_log_year=_get(raw, "decision_log_year", int, ""),
        cache_dir=Path(_get(paths, "cache_dir", str, "paths")),
        index_path=Path(_get(paths, "index_path", str, "paths")),
        sources=sources,
        decisions=_parse_decisions(raw.get("decisions"), names),
        aliases=_parse_aliases(raw.get("aliases")),
        contracts=_parse_contracts(raw.get("contracts"), names),
    )


def _parse_contracts(raw: Any, index_source_names: list[str]) -> ContractsConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("contracts: 매핑이 아니다")
    where = "contracts"
    sources = tuple(
        _parse_source(s, f"{where}.sources[{i}]")
        for i, s in enumerate(_get(raw, "sources", list, where))
    )
    names = [s.name for s in sources]
    duplicates = sorted({n for n in names if names.count(n) > 1})
    if duplicates:
        raise ConfigError(f"{where}.sources: 이름이 겹친다: {', '.join(duplicates)}")
    # 캐시 폴더 이름이 소스 이름이다. 같은 이름이면 문서 색인의 기준 커밋을 덮어쓴다.
    shared = sorted(set(names) & set(index_source_names))
    if shared:
        raise ConfigError(f"{where}.sources: sources와 이름이 겹친다: {', '.join(shared)}")

    def known(name: str, label: str) -> str:
        if name not in names:
            raise ConfigError(f"{label}: contracts.sources에 없는 이름이다: {name!r}")
        return name

    services = []
    for i, s in enumerate(_get(raw, "services", list, where)):
        label = f"{where}.services[{i}]"
        if not isinstance(s, dict):
            raise ConfigError(f"{label}: 매핑이 아니다")
        spec = _get(s, "spec", dict, label)
        server = _get(s, "server", dict, label)
        services.append(
            ServiceContract(
                name=_get(s, "name", str, label),
                spec_source=known(_get(spec, "source", str, f"{label}.spec"), f"{label}.spec"),
                spec_path=_get(spec, "path", str, f"{label}.spec"),
                server_source=known(
                    _get(server, "source", str, f"{label}.server"), f"{label}.server"
                ),
                server_kind=_choice(server, "kind", SERVER_KINDS, f"{label}.server"),
            )
        )
    service_names = [s.name for s in services]
    if len(set(service_names)) != len(service_names):
        raise ConfigError(f"{where}.services: 이름이 겹친다")

    callers = []
    for i, c in enumerate(raw.get("callers") or []):
        label = f"{where}.callers[{i}]"
        if not isinstance(c, dict):
            raise ConfigError(f"{label}: 매핑이 아니다")
        target = _get(c, "target", str, label)
        if target not in service_names:
            raise ConfigError(f"{label}.target: services에 없는 이름이다: {target!r}")
        callers.append(
            CallerContract(
                name=_get(c, "name", str, label),
                target=target,
                source=known(_get(c, "source", str, label), label),
                kind=_choice(c, "kind", CALLER_KINDS, label),
                paths=_str_list(c, "paths", label, required=False),
                path_prefix=_get(c, "path_prefix", str, label) if "path_prefix" in c else "/",
                functions=_str_list(c, "functions", label, required=False),
            )
        )
    return ContractsConfig(sources, tuple(services), tuple(callers))


def _choice(raw: dict, key: str, choices: tuple[str, ...], where: str) -> str:
    value = _get(raw, key, str, where)
    if value not in choices:
        raise ConfigError(f"{where}.{key}: {' · '.join(choices)} 중 하나다: {value!r}")
    return value


def _parse_decisions(raw: Any, source_names: list[str]) -> DecisionsConfig | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ConfigError("decisions: 매핑이 아니다")
    source = _get(raw, "source", str, "decisions")
    if source not in source_names:
        raise ConfigError(f"decisions.source: sources에 없는 이름이다: {source!r}")
    corrections = raw.get("corrections")
    if corrections is not None and not isinstance(corrections, str):
        raise ConfigError(f"decisions.corrections: 경로 문자열이어야 한다: {corrections!r}")
    return DecisionsConfig(
        source=source,
        log=_get(raw, "log", str, "decisions"),
        corrections=Path(corrections) if corrections else None,
    )


def _parse_aliases(raw: Any) -> tuple[tuple[str, ...], ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise ConfigError("aliases: 목록이어야 한다")
    groups = []
    for i, group in enumerate(raw):
        if not isinstance(group, list) or len(group) < 2:
            raise ConfigError(f"aliases[{i}]: 말 두 개 이상의 목록이어야 한다")
        if not all(isinstance(term, str) and term for term in group):
            raise ConfigError(f"aliases[{i}]: 빈 값이 아닌 문자열만 쓴다")
        groups.append(tuple(group))
    return tuple(groups)


def _parse_source(raw: Any, where: str) -> Source:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where}: 매핑이 아니다")

    repo = _get(raw, "repo", str, where)
    if not REPO_PATTERN.fullmatch(repo):
        raise ConfigError(f"{where}.repo: 'owner/name' 형식이 아니다: {repo!r}")

    # YAML은 숫자로만 된 커밋(예: 1234567)을 int로 읽으므로 문자열로 맞춘다.
    ref = str(_get(raw, "ref", (str, int), where))
    if not COMMIT_PATTERN.fullmatch(ref):
        raise ConfigError(
            f"{where}.ref: 브랜치 이름이 아니라 커밋(16진수 7~40자리)을 적는다: {ref!r}"
        )

    include = _str_list(raw, "include", where, required=True)
    if not include:
        raise ConfigError(f"{where}.include: 비어 있다")

    return Source(
        name=_get(raw, "name", str, where),
        repo=repo,
        ref=ref,
        include=include,
        exclude=_str_list(raw, "exclude", where, required=False),
        extensions=_str_list(raw, "extensions", where, required=False) or (".md",),
    )


def _get(raw: dict, key: str, kind: type | tuple[type, ...], where: str) -> Any:
    label = f"{where}.{key}" if where else key
    if key not in raw:
        raise ConfigError(f"{label}: 없다")
    value = raw[key]
    # bool은 int의 하위 타입이라 따로 막는다.
    if isinstance(value, bool) or not isinstance(value, kind):
        raise ConfigError(f"{label}: {_kind_name(kind)} 타입이어야 한다: {value!r}")
    return value


def _str_list(raw: dict, key: str, where: str, *, required: bool) -> tuple[str, ...]:
    if key not in raw and not required:
        return ()
    items = _get(raw, key, list, where)
    for i, item in enumerate(items):
        if not isinstance(item, str):
            raise ConfigError(f"{where}.{key}[{i}]: 문자열이 아니다: {item!r}")
    return tuple(items)


def _kind_name(kind: type | tuple[type, ...]) -> str:
    kinds = kind if isinstance(kind, tuple) else (kind,)
    return "·".join(k.__name__ for k in kinds)
