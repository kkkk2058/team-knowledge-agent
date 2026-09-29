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
class Config:
    team: str
    decision_log_year: int
    cache_dir: Path  # 레포 루트 기준
    index_path: Path  # 레포 루트 기준
    sources: tuple[Source, ...]

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
    )


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
