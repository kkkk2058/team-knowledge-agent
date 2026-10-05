from pathlib import Path

import pytest
from helpers import git

from tka.config import (
    CallerContract,
    Config,
    ConfigError,
    ContractsConfig,
    ServiceContract,
    Source,
)
from tka.contracts import compare, parse
from tka.contracts.__main__ import main
from tka.contracts.parse import Endpoint
from tka.contracts.report import build_report, render_markdown
from tka.ingest.fetch import FetchError

# ── 경로 ──────────────────────────────────────────────────────────


def test_normalize_path_hides_variable_names_query_and_trailing_slash():
    assert parse.normalize_path("/a/{id}/b/") == "/a/{}/b"
    assert parse.normalize_path("/a/${item.id}?x=${q}") == "/a/{}"
    assert parse.normalize_path("//a//b") == "/a/b"
    assert parse.normalize_path("/") == "/"


def test_path_fits_treats_variables_as_any_segment():
    assert compare.path_fits("/auth/kakao/login", "/auth/{}/login")
    assert compare.path_fits("/a/{}", "/a/1")
    assert not compare.path_fits("/auth/kakao", "/auth/{}/login")
    assert not compare.path_fits("/auth/kakao/logout", "/auth/{}/login")


# ── 명세 ──────────────────────────────────────────────────────────

SPEC = """# API

| # | 엔드포인트 | 설명 |
|---|---|---|
| ① | `POST /search` | 검색한다 |
| ④ | `GET /recommendations/feed` | 피드 |

본문에서 `POST /search`를 다시 말해도 정의가 아니다.

<details>
<summary><code>PATCH /api/v1/cart/items/{cartItemId}</code> — 수량 변경</summary>
</details>
"""


def test_spec_reads_table_cells_and_summaries_only():
    found = parse.spec_endpoints(SPEC, "wiki/spec.md")

    assert [(e.method, e.path, e.note, e.where) for e in found] == [
        ("POST", "/search", "① 검색한다", "wiki/spec.md:5"),
        ("GET", "/recommendations/feed", "④ 피드", "wiki/spec.md:6"),
        ("PATCH", "/api/v1/cart/items/{cartItemId}", "수량 변경", "wiki/spec.md:11"),
    ]


# ── 서버 ──────────────────────────────────────────────────────────


def test_fastapi_router_prefix_app_route_and_include_prefix():
    files = {
        "app/main.py": (
            "app = FastAPI()\n"
            "app.include_router(chat.router, dependencies=[Depends(auth)])\n"
            "app.include_router(admin.router, prefix='/admin')\n"
            '@app.get("/health")\n'
        ),
        "app/routers/chat.py": (
            'router = APIRouter(\n    dependencies=[Depends(x)], prefix="/recommendations"\n)\n'
            '@router.post("/chat")\n'
            '# @router.get("/old") 주석이 아니라 데코레이터 줄만 본다\n'
        ),
        "app/routers/admin.py": 'router = APIRouter()\n@router.delete("/jobs/{job_id}")\n',
        "app/other.py": '@cache.get("/not-a-route")\n',
    }

    found = parse.fastapi_routes(files, "AI")

    assert sorted(e.label() for e in found) == [
        "DELETE /admin/jobs/{job_id}",
        "GET /health",
        "POST /recommendations/chat",
    ]
    assert next(e for e in found if e.path == "/health").where == "AI/app/main.py:4"


JAVA = """package x;

@RestController
@RequestMapping("/api/v1/cart")
@RequiredArgsConstructor
public class CartController {
    @GetMapping
    public X get() {}

    @PutMapping(value = "/items/{cartItemId}", produces = "application/json")
    public X put() {}

    // @DeleteMapping("/old")
    @RequestMapping(path = "/legacy", method = RequestMethod.POST)
    public X legacy() {}

    @RequestMapping("/any")
    public X any() {}
}
"""


def test_spring_class_prefix_and_method_mappings():
    found = parse.spring_routes(
        {"src/CartController.java": JAVA, "src/Dto.java": "record Dto() {}"}, "BE"
    )

    assert [(e.label(), e.where) for e in found] == [
        ("GET /api/v1/cart", "BE/src/CartController.java:7"),
        ("PUT /api/v1/cart/items/{cartItemId}", "BE/src/CartController.java:10"),
        ("POST /api/v1/cart/legacy", "BE/src/CartController.java:14"),
        ("? /api/v1/cart/any", "BE/src/CartController.java:17"),
    ]


# ── 호출 ──────────────────────────────────────────────────────────

CLIENT = """class FeedClient {
    private static final String FEED_PATH = "/recommendations/feed";
    final static String CHAT = "/recommendations/chat";

    Feed feed() {
        return restClient.get().uri(uriBuilder -> {
            return uriBuilder.path(FEED_PATH).queryParam("size", 20).build();
        }).retrieve();
    }
    Chat chat() { return restClient.post().uri(CHAT).body(x).retrieve(); }
    Search search() { return restClient.post().uri("/search").retrieve(); }
    Other other() { return restClient.get().uri(baseUrl + suffix).retrieve(); }
}
"""


def test_restclient_reads_constants_literals_and_reports_unresolved():
    calls, unresolved = parse.restclient_calls({"c/FeedClient.java": CLIENT}, "BE")

    assert [e.label() for e in calls] == [
        "GET /recommendations/feed",
        "POST /recommendations/chat",
        "POST /search",
    ]
    assert unresolved == ["BE/c/FeedClient.java:12"]


TS = """
export async function a() { return fetchWithAuth("/api/v1/cart", { signal }); }
export async function b() {
  return fetchWithAuth(`/api/v1/cart/items/${id}`, {
    method: "PUT",
    body: JSON.stringify({ q: f(x) }),
  });
}
export const c = () => sendMutation(`/api/v1/addresses/${id}/default`, { method: "post" });
export const d = () => removeItem(`/api/v1/cart/items/${id}`);
const url = createApiUrl("/api/v1/auth/kakao/login");
const e = () => fetchPublic(`/api/v1/items?${params.toString()}`);
// fetchWithAuth("/api/v1/old")
const route = "/login";
"""


def test_fetch_methods_from_init_default_get_and_unknown_wrappers():
    found = parse.fetch_calls(
        {"src/api.ts": TS}, "FE", prefix="/api/v1", functions=["fetchWithAuth", "fetchPublic"]
    )

    assert [e.label() for e in found] == [
        "GET /api/v1/cart",
        "PUT /api/v1/cart/items/${id}",
        "POST /api/v1/addresses/${id}/default",
        "? /api/v1/cart/items/${id}",
        "? /api/v1/auth/kakao/login",
        "GET /api/v1/items?${params.toString()}",
    ]


# ── 대조 ──────────────────────────────────────────────────────────


def _e(label: str, where: str = "x") -> Endpoint:
    method, path = label.split(" ", 1)
    return Endpoint(None if method == "?" else method, path, where)


def test_compare_spec_pairs_exact_first_then_method_differs():
    spec = [_e("PATCH /a/{id}"), _e("DELETE /a/{id}"), _e("GET /addresses"), _e("GET /gone")]
    code = [_e("DELETE /a/{x}"), _e("PUT /a/{x}"), _e("GET /user-addresses"), _e("GET /new")]

    r = compare.compare_spec("BE", "spec.md", spec, code)

    assert [(p.left.label(), p.right.label()) for p in r.matched] == [
        ("DELETE /a/{id}", "DELETE /a/{x}")
    ]
    assert [(p.left.label(), p.right.label()) for p in r.method_differs] == [
        ("PATCH /a/{id}", "PUT /a/{x}")
    ]
    assert [(s.label(), h.label() if h else None) for s, h in r.spec_only] == [
        ("GET /addresses", "GET /user-addresses"),
        ("GET /gone", None),
    ]
    assert [e.label() for e in r.code_only] == ["GET /user-addresses", "GET /new"]


def test_similar_hint_ignores_shared_api_prefix():
    spec = [_e("POST /api/v1/payments")]
    code = [_e("POST /api/v1/recommend/chat")]

    r = compare.compare_spec("BE", "spec.md", spec, code)

    assert r.spec_only[0][1] is None


def test_compare_calls_and_uncalled():
    server = [
        _e("POST /auth/{type}/login"),
        _e("PUT /cart/items/{id}"),
        _e("DELETE /cart/items/{id}"),
        _e("GET /orders"),
    ]
    calls = [
        _e("? /auth/kakao/login"),
        _e("? /cart/items/${id}"),
        _e("POST /orders"),
        _e("GET /missing"),
    ]

    r = compare.compare_calls("FE→BE", "BE", calls, server, ["fe.ts:9"])

    assert [p.left.label() for p in r.matched] == ["? /auth/kakao/login", "? /cart/items/${id}"]
    assert [(p.left.label(), p.right.label()) for p in r.method_differs] == [
        ("POST /orders", "GET /orders")
    ]
    assert [e.label() for e in r.missing] == ["GET /missing"]
    # 메서드를 모르는 호출은 같은 경로의 PUT·DELETE를 모두 부를 수 있다
    assert [e.label() for e in compare.uncalled(server, [r])] == ["GET /orders"]


# ── 설정과 전체 흐름 ──────────────────────────────────────────────


def _repo(root: Path, name: str, files: dict[str, str]) -> str:
    repo = root / ".cache" / "sources" / name
    for path, text in files.items():
        (repo / path).parent.mkdir(parents=True, exist_ok=True)
        (repo / path).write_text(text, encoding="utf-8")
    git(repo, "init", "-q", "-b", "main")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "init")
    return git(repo, "rev-parse", "--short=7", "HEAD")


def _source(name: str, ref: str, include: str, ext: str) -> Source:
    return Source(name, f"org/{name}", ref, (include,), (), (ext,))


@pytest.fixture
def contract_config(tmp_path) -> Config:
    wiki = _repo(tmp_path, "c-wiki", {"docs/be.md": "| 1 | `GET /api/v1/cart` | 조회 |\n"})
    be = _repo(
        tmp_path,
        "be",
        {
            "src/CartController.java": (
                '@RequestMapping("/api/v1/cart")\npublic class C {\n'
                '  @GetMapping\n  X a() {}\n  @PostMapping("/items")\n  X b() {}\n}\n'
            )
        },
    )
    fe = _repo(tmp_path, "fe", {"src/cart.ts": 'fetchWithAuth("/api/v1/carts");\n'})
    return Config(
        team="t",
        decision_log_year=2026,
        cache_dir=Path(".cache/sources"),
        index_path=Path("data/index.sqlite"),
        sources=(),
        contracts=ContractsConfig(
            sources=(
                _source("c-wiki", wiki, "docs/", ".md"),
                _source("be", be, "src/", ".java"),
                _source("fe", fe, "src/", ".ts"),
            ),
            services=(ServiceContract("BE", "c-wiki", "docs/be.md", "be", "spring"),),
            callers=(
                CallerContract("FE→BE", "BE", "fe", "fetch", (), "/api/v1", ("fetchWithAuth",)),
            ),
        ),
    )


def test_build_report_and_markdown(tmp_path, contract_config):
    report = build_report(contract_config, tmp_path)

    s, c = report.services[0], report.callers[0]
    assert [p.right.label() for p in s.matched] == ["GET /api/v1/cart"]
    assert [e.label() for e in s.code_only] == ["POST /api/v1/cart/items"]
    assert [e.label() for e in c.missing] == ["GET /api/v1/carts"]
    assert set(report.commits) == {"c-wiki", "be", "fe"}

    text = render_markdown(report, "2026-10-06")
    assert "| BE | 1 | 2 | 1 | 0 | 0 | 1 |" in text
    assert "| FE→BE | 1 | 0 | 0 | 1 | 0 |" in text
    assert "| `GET /api/v1/carts` | fe/src/cart.ts:1 |" in text


def test_build_report_stops_when_source_is_not_at_pinned_commit(tmp_path, contract_config):
    git(tmp_path / ".cache" / "sources" / "be", "commit", "-q", "--allow-empty", "-m", "새 커밋")

    with pytest.raises(FetchError, match="기준 커밋이 아니다"):
        build_report(contract_config, tmp_path)


def test_check_command_without_contracts_stops(tmp_path, capsys):
    config = tmp_path / "c.yaml"
    config.write_text(
        "team: t\ndecision_log_year: 2026\n"
        "paths: {cache_dir: .cache/sources, index_path: data/i.sqlite}\n"
        "sources: [{name: wiki, repo: o/w, ref: abcdef1, include: [docs/]}]\n",
        encoding="utf-8",
    )

    assert main(["check", "--config", str(config), "--root", str(tmp_path)]) == 1
    assert "contracts가 없다" in capsys.readouterr().err


def test_contract_source_names_must_differ_from_index_sources(tmp_path):
    from tka.config import load_config

    config = tmp_path / "c.yaml"
    config.write_text(
        "team: t\ndecision_log_year: 2026\n"
        "paths: {cache_dir: .cache/sources, index_path: data/i.sqlite}\n"
        "sources: [{name: wiki, repo: o/w, ref: abcdef1, include: [docs/]}]\n"
        "contracts:\n"
        "  sources: [{name: wiki, repo: o/w, ref: 1234567, include: [docs/]}]\n"
        "  services: []\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match="sources와 이름이 겹친다: wiki"):
        load_config(config)
