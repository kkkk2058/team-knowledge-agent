from tka import core
from tka.contracts.compare import Report, compare_calls, compare_spec
from tka.contracts.lookup import api_entries, find_api
from tka.contracts.parse import Endpoint


def _e(label: str, where: str, note: str = "") -> Endpoint:
    method, path = label.split(" ", 1)
    return Endpoint(None if method == "?" else method, path, where, note)


def _report() -> Report:
    spec = [
        _e("PATCH /cart/items/{id}", "wiki/fs.md:3", "수량 변경"),
        _e("DELETE /cart/items/{id}", "wiki/fs.md:4", "장바구니 삭제"),
        _e("GET /addresses", "wiki/fs.md:5", "배송지 목록"),
        _e("GET /points", "wiki/fs.md:6", "포인트"),
    ]
    code = [
        _e("PUT /cart/items/{cartItemId}", "BE/Cart.java:10"),
        _e("DELETE /cart/items/{cartItemId}", "BE/Cart.java:20"),
        _e("GET /user-addresses", "BE/Address.java:5"),
        _e("GET /health", "BE/Support.java:3"),
    ]
    calls = [
        _e("PUT /cart/items/${id}", "FE/cart.ts:9"),
        _e("? /cart/items/${id}", "FE/cart.ts:12"),  # DELETE 래퍼: 메서드를 모른다
        _e("GET /user-addresses", "FE/address.ts:2"),
        _e("GET /missing", "FE/x.ts:1"),
    ]
    return Report(
        {"wiki": "aaaaaaa", "BE": "bbbbbbb", "FE": "ccccccc"},
        (compare_spec("BE", "wiki/fs.md", spec, code),),
        (compare_calls("FE→BE", "BE", calls, code),),
    )


def test_unknown_method_call_is_shown_under_every_server_method_of_that_path():
    entries = {(e.main.label(), e.status): e for e in api_entries(_report())}

    put = entries[("PUT /cart/items/{cartItemId}", "메서드 다름")]
    delete = entries[("DELETE /cart/items/{cartItemId}", "일치")]

    assert [c.endpoint.where for c in put.calls] == ["FE/cart.ts:9", "FE/cart.ts:12"]
    assert [c.endpoint.where for c in delete.calls] == ["FE/cart.ts:12"]


def test_find_by_path_word_and_method():
    entries = api_entries(_report())

    def labels(query, method=""):
        return [e.main.label() for e in find_api(entries, query, method)]

    assert labels("/cart/items/7") == [
        "PUT /cart/items/{cartItemId}",
        "DELETE /cart/items/{cartItemId}",
    ]
    assert labels("/cart") == labels("/cart/items/7")  # 그 아래 경로도 찾는다
    assert labels("장바구니") == ["DELETE /cart/items/{cartItemId}"]  # 명세 설명 낱말
    # 메서드는 명세·코드로 거른다. 메서드를 모르는 호출이 붙어 있어도 PUT은 DELETE로 안 나온다
    assert labels("cart", "DELETE") == ["DELETE /cart/items/{cartItemId}"]
    assert labels("missing", "GET") == ["GET /missing"]


def test_empty_query_lists_problems_most_severe_first():
    text, shown = core.check_api(_report())

    assert [(e.status, e.main.label()) for e in shown] == [
        ("서버에 없음", "GET /missing"),
        ("메서드 다름", "PUT /cart/items/{cartItemId}"),
        ("명세에만", "GET /addresses"),
        ("명세에만", "GET /points"),
        ("코드에만", "GET /user-addresses"),
        ("코드에만", "GET /health"),
    ]
    assert "어긋난 곳 6개 (일치 1개는 뺐다)" in text
    assert "기준: wiki@aaaaaaa · BE@bbbbbbb · FE@ccccccc" in text


def test_entry_text_has_spec_code_calls_and_citations():
    text, _ = core.check_api(_report(), "/cart/items/1", "PUT")

    assert text.endswith(
        "[BE] PUT /cart/items/{cartItemId} — 메서드가 다르다\n"
        "  명세: PATCH /cart/items/{id} — 수량 변경 — wiki/fs.md:3@aaaaaaa\n"
        "  코드: PUT /cart/items/{cartItemId} — BE/Cart.java:10@bbbbbbb\n"
        "  호출: FE→BE PUT /cart/items/${id} — FE/cart.ts:9@ccccccc\n"
        "  호출: FE→BE ? /cart/items/${id} (래퍼 함수라 메서드를 코드에서 못 읽음) "
        "— FE/cart.ts:12@ccccccc"
    )


def test_spec_only_hint_uncalled_and_missing_call_text():
    text, _ = core.check_api(_report())

    assert (
        "[BE] GET /addresses — 명세에만 있다 (코드에 없음)\n"
        "  명세: GET /addresses — 배송지 목록 — wiki/fs.md:5@aaaaaaa\n"
        "  코드: 없음. 비슷한 코드 경로: GET /user-addresses — BE/Address.java:5@bbbbbbb"
    ) in text
    assert (
        "[BE] GET /health — 코드에만 있다 (명세에 없음)\n"
        "  명세: 없음 (wiki/fs.md에서 찾지 못함)\n"
        "  코드: GET /health — BE/Support.java:3@bbbbbbb\n"
        "  호출: 설정한 호출 코드 중 부르는 곳 없음"
    ) in text
    assert (
        "[BE] GET /missing — 서버에 없는 경로를 부른다\n"
        "  명세: 없음 (wiki/fs.md에서 찾지 못함)\n"
        "  코드: BE 서버에 이 경로가 없다\n"
        "  호출: FE→BE GET /missing — FE/x.ts:1@ccccccc"
    ) in text


def test_no_match_and_limit():
    none, found = core.check_api(_report(), "/nothing")
    limited, shown = core.check_api(_report(), limit=2)

    assert found == []
    assert "맞는 API가 없다" in none
    assert len(shown) == 2
    assert limited.endswith("…외 4개. 경로나 메서드로 좁혀 다시 부른다.")


def test_service_without_callers_does_not_claim_nobody_calls():
    spec = [_e("POST /search", "wiki/ai.md:1")]
    code = [_e("POST /search", "AI/main.py:2")]
    report = Report({}, (compare_spec("AI", "wiki/ai.md", spec, code),), ())

    text, _ = core.check_api(report, "search")

    assert "  호출: 이 서비스를 부르는 코드는 대조하지 않는다" in text
