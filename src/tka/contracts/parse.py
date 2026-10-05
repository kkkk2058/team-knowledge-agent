"""API 대조표의 재료: 명세 문서·서버 코드·호출 코드에서 엔드포인트를 뽑는다 (implementation.md ⑨).

LLM을 쓰지 않는다(plan.md D3). 정규식으로 흔한 모양만 읽으므로 특이하게 쓴 코드는 놓친다.
놓친 것은 대조표에 "명세에만 있음"이나 "경로를 못 읽은 호출"로 드러난다.

- 명세: 표 칸이나 <summary> 안에 `METHOD /경로`만 적힌 줄
- FastAPI: `@router.get("/x")`, `APIRouter(prefix=...)`, `include_router(..., prefix=...)`
- Spring 서버: 클래스의 `@RequestMapping` + 메서드의 `@GetMapping` 등
- Spring 호출(RestClient·WebClient): `.post().uri(PATH)`, PATH는 같은 파일의 문자열 상수
- fetch 호출(TS): 설정한 접두어로 시작하는 문자열. 메서드는 같은 호출의 `method: "POST"`
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath

METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
_METHOD = "|".join(METHODS)


@dataclass(frozen=True)
class Endpoint:
    method: str | None  # None: 코드에서 정할 수 없었다 (래퍼 함수에 경로만 넘긴 호출 등)
    path: str  # 적힌 그대로 (쿼리는 뺀다)
    where: str  # 레포/경로:줄
    note: str = ""  # 명세 설명

    @property
    def key(self) -> str:
        return normalize_path(self.path)

    def label(self) -> str:
        return f"{self.method or '?'} {self.path}"


def normalize_path(path: str) -> str:
    """경로 변수 이름·쿼리·끝 슬래시를 지워 비교할 수 있게 한다. `/a/{id}/` → `/a/{}`"""
    path = path.split("?", 1)[0]
    path = re.sub(r"\$\{[^}]*\}|\{[^}]*\}", "{}", path)
    path = re.sub(r"/+", "/", path)
    return path.rstrip("/") or "/"


# ── 명세 ──────────────────────────────────────────────────────────

_SPEC_TABLE = re.compile(rf"^\|\s*([^|]*?)\s*\|\s*`({_METHOD}) (/[^`\s]*)`\s*\|\s*([^|]*)")
_SPEC_SUMMARY = re.compile(
    rf"<summary><code>({_METHOD}) (/[^<\s]*)</code>\s*(?:—\s*)?(.*?)</summary>"
)


def spec_endpoints(text: str, where: str) -> list[Endpoint]:
    """명세 문서의 엔드포인트 정의 줄. 본문 문장 속 언급은 세지 않는다."""
    found = []
    for n, line in enumerate(text.split("\n"), 1):
        if m := _SPEC_TABLE.match(line):
            label, method, path, desc = m.groups()
            note = " ".join(x for x in (label, desc.strip()) if x)
        elif m := _SPEC_SUMMARY.search(line):
            method, path, note = m.groups()
        else:
            continue
        found.append(Endpoint(method, path, f"{where}:{n}", note.strip()))
    return _dedupe(found)


# ── 서버: FastAPI ─────────────────────────────────────────────────

_FASTAPI_APP = re.compile(r"^(\w+)\s*=\s*FastAPI\(", re.M)
_FASTAPI_ROUTER = re.compile(r"^(\w+)\s*=\s*APIRouter\(", re.M)
_FASTAPI_ROUTE = re.compile(
    r"^\s*@(\w+)\.(get|post|put|patch|delete)\(\s*[\"']([^\"']*)[\"']", re.M
)
_FASTAPI_INCLUDE = re.compile(r"\.include_router\(")
_PREFIX_ARG = re.compile(r"\bprefix\s*=\s*[\"']([^\"']*)[\"']")


def fastapi_routes(files: Mapping[str, str], repo: str) -> list[Endpoint]:
    """files: 레포 경로 → 내용. include_router의 prefix는 `모듈.router`의 모듈 파일에 붙인다."""
    include_prefix: dict[str, str] = {}
    for text in files.values():
        for m in _FASTAPI_INCLUDE.finditer(text):
            args = text[m.end() : _call_end(text, m.end() - 1)]
            target = re.match(r"\s*([\w.]+)", args)
            prefix = _PREFIX_ARG.search(args)
            if target and prefix and target.group(1).endswith(".router"):
                include_prefix[target.group(1).rsplit(".", 2)[-2]] = prefix.group(1)

    found = []
    for path, text in files.items():
        apps = set(_FASTAPI_APP.findall(text))
        routers = {}
        for m in _FASTAPI_ROUTER.finditer(text):
            prefix = _PREFIX_ARG.search(text[m.end() : _call_end(text, m.end() - 1)])
            routers[m.group(1)] = prefix.group(1) if prefix else ""
        outer = include_prefix.get(PurePosixPath(path).stem, "")
        for m in _FASTAPI_ROUTE.finditer(text):
            var, method, sub = m.groups()
            if var in routers:
                full = _join(outer, routers[var], sub)
            elif var in apps:
                full = _join(sub)
            else:
                continue
            found.append(Endpoint(method.upper(), full, f"{repo}/{path}:{_line(text, m.start())}"))
    return _dedupe(found)


# ── 서버: Spring ──────────────────────────────────────────────────

_SPRING_MAPPING = re.compile(r"@(Request|Get|Post|Put|Patch|Delete)Mapping\b(\s*\()?")
_SPRING_TYPE = re.compile(
    r"^\s*(?:(?:public|protected|private|final|abstract|static)\s+)*(?:class|interface)\s+\w+",
    re.M,
)
_REQUEST_METHOD = re.compile(rf"RequestMethod\.({_METHOD})")
_STRING = re.compile(r'"([^"\\]*)"')


def spring_routes(files: Mapping[str, str], repo: str) -> list[Endpoint]:
    """클래스 선언 앞의 @RequestMapping은 접두어, 뒤의 매핑은 엔드포인트다.

    메서드에 붙은 @RequestMapping은 method=RequestMethod.X가 없으면 메서드를 모른다(None).
    경로를 여러 개 적은 매핑은 첫 경로만 읽는다.
    """
    found = []
    for path, text in files.items():
        decl = _SPRING_TYPE.search(text)
        if not decl:
            continue
        prefix = ""
        for m in _SPRING_MAPPING.finditer(text):
            if _in_comment(text, m.start()):
                continue
            args = text[m.end() : _call_end(text, m.end() - 1)] if m.group(2) else ""
            literal = _STRING.search(args)
            sub = literal.group(1) if literal else ""
            if m.start() < decl.start():
                if m.group(1) == "Request":
                    prefix = sub
                continue
            if m.group(1) == "Request":
                method_arg = _REQUEST_METHOD.search(args)
                method = method_arg.group(1) if method_arg else None
            else:
                method = m.group(1).upper()
            where = f"{repo}/{path}:{_line(text, m.start())}"
            found.append(Endpoint(method, _join(prefix, sub), where))
    return _dedupe(found)


# ── 호출: Spring RestClient·WebClient ─────────────────────────────

_JAVA_CONST = re.compile(r"\b(?:static\s+final|final\s+static)\s+String\s+(\w+)\s*=\s*\"([^\"]*)\"")
_HTTP_CALL = re.compile(r"\.(get|post|put|patch|delete)\(\)\s*\.uri\(")


def restclient_calls(files: Mapping[str, str], repo: str) -> tuple[list[Endpoint], list[str]]:
    """(호출, 경로를 못 읽은 호출 위치). 경로는 uri(...) 안의 문자열이나 같은 파일의 상수."""
    calls, unresolved = [], []
    for path, text in files.items():
        consts = dict(_JAVA_CONST.findall(text))
        for m in _HTTP_CALL.finditer(text):
            args = text[m.end() : _call_end(text, m.end() - 1)]
            where = f"{repo}/{path}:{_line(text, m.start())}"
            target = _first_path(args, consts)
            if target is None:
                unresolved.append(where)
                continue
            calls.append(Endpoint(m.group(1).upper(), target, where))
    return calls, unresolved


def _first_path(args: str, consts: Mapping[str, str]) -> str | None:
    for token in re.finditer(r'"([^"\\]*)"|\b([A-Za-z_]\w*)\b', args):
        literal, name = token.groups()
        value = literal if literal is not None else consts.get(name or "")
        if value and value.startswith("/"):
            return value
    return None


# ── 호출: fetch (TypeScript) ──────────────────────────────────────

_METHOD_ARG = re.compile(r"\bmethod\s*:\s*[\"'`](\w+)[\"'`]")


def fetch_calls(
    files: Mapping[str, str], repo: str, *, prefix: str, functions: Iterable[str]
) -> list[Endpoint]:
    """prefix로 시작하는 문자열·템플릿 리터럴을 API 경로로 본다.

    리터럴이 어떤 함수의 첫 인자면 그 호출 인자에서 `method:`를 찾는다. 없으면 functions에
    든 함수(fetch처럼 기본이 GET)는 GET, 그 밖의 함수는 모른다(None).
    """
    literal = re.compile(r"([\"'`])(" + re.escape(prefix) + r"[^\"'`]*)\1")
    fetchers = set(functions)
    found = []
    for path, text in files.items():
        for m in literal.finditer(text):
            if _in_comment(text, m.start()):
                continue
            func, open_paren = _called_with_first_arg(text, m.start())
            method = None
            if func is not None:
                arg = _METHOD_ARG.search(text[open_paren + 1 : _call_end(text, open_paren)])
                if arg:
                    method = arg.group(1).upper()
                elif func in fetchers:
                    method = "GET"
            where = f"{repo}/{path}:{_line(text, m.start())}"
            found.append(Endpoint(method, m.group(2), where))
    return _dedupe(found)


def _called_with_first_arg(text: str, start: int) -> tuple[str | None, int]:
    """start의 리터럴이 `name(` 바로 다음 첫 인자면 (name, 여는 괄호 위치)."""
    i = start - 1
    while i >= 0 and text[i].isspace():
        i -= 1
    if i < 0 or text[i] != "(":
        return None, -1
    name = re.search(r"([A-Za-z_$][\w$]*)\s*$", text[:i])
    return (name.group(1), i) if name else (None, -1)


# ── 공통 ──────────────────────────────────────────────────────────


def _call_end(text: str, open_paren: int) -> int:
    """open_paren의 `(`와 짝인 `)` 위치. 문자열 안 괄호는 세지 않는다. 짝이 없으면 끝."""
    depth, i, quote = 0, open_paren, None
    while i < len(text):
        c = text[i]
        if quote:
            if c == "\\":
                i += 1
            elif c == quote:
                quote = None
        elif c in "\"'`":
            quote = c
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return len(text)


def _in_comment(text: str, pos: int) -> bool:
    """같은 줄에서 pos 앞에 `//`가 있으면 주석으로 본다 (블록 주석은 보지 않는다)."""
    line_start = text.rfind("\n", 0, pos) + 1
    return "//" in text[line_start:pos].replace("://", "")


def _join(*parts: str) -> str:
    joined = "/".join(p.strip("/") for p in parts if p.strip("/"))
    return "/" + joined


def _line(text: str, pos: int) -> int:
    return text.count("\n", 0, pos) + 1


def _dedupe(endpoints: list[Endpoint]) -> list[Endpoint]:
    """같은 메서드·경로는 처음 나온 위치 하나만."""
    seen: set[tuple[str | None, str]] = set()
    kept = []
    for e in endpoints:
        if (e.method, e.key) not in seen:
            seen.add((e.method, e.key))
            kept.append(e)
    return kept
