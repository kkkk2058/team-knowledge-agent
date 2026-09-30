import json
from pathlib import Path

from tka.calllog import CallLog, is_empty, read_calls, render_calls


def _write(path: Path, *records, raw: str = "") -> None:
    lines = [json.dumps(r, ensure_ascii=False) for r in records]
    path.write_text("\n".join(lines) + "\n" + raw, encoding="utf-8")


def _record(tool, at="2026-09-30T15:10:00+09:00", **kw):
    base = {
        "at": at,
        "via": "mcp",
        "tool": tool,
        "args": {"query": "상품 목록 페이지 방식"},
        "commit": "6109d58abc",
        "returned": ["docs/fs/2-api/spec.md:160-163"],
        "elapsed_ms": 320,
    }
    base.update(kw)
    return base


def test_write_appends_one_line_with_time_and_via(tmp_path):
    path = tmp_path / "data" / "calls.jsonl"
    log = CallLog(path, "cli")

    log.write(tool="search_docs", returned=[])
    log.write(tool="get_decision", returned=["a:1"])

    records, bad = read_calls(path)
    assert bad == 0
    assert [(r["via"], r["tool"]) for r in records] == [
        ("cli", "search_docs"),
        ("cli", "get_decision"),
    ]
    assert records[0]["at"].startswith("20")


def test_read_skips_broken_lines_and_counts_them(tmp_path):
    path = tmp_path / "calls.jsonl"
    _write(path, _record("search_docs"), raw='{"tool": "ask", "at": "2026-\n[1, 2]\n')

    records, bad = read_calls(path)

    assert [r["tool"] for r in records] == ["search_docs"]
    assert bad == 2
    assert read_calls(tmp_path / "없음.jsonl") == ([], 0)


def test_empty_means_no_evidence_or_unknown():
    assert is_empty(_record("search_docs", returned=[]))
    assert is_empty(_record("ask", unknown=True, returned=["x"]))
    assert not is_empty(_record("ask", unknown=False))


def test_render_summarizes_and_lists_newest_first(tmp_path):
    records = [
        _record("search_docs"),
        _record("get_decision", at="2026-09-30T15:11:00+09:00", returned=[]),
        _record(
            "ask",
            at="2026-10-01T09:12:00+09:00",
            via="cli",
            args={"question": "결제 PG사는?", "model": "m"},
            unknown=True,
            returned=[],
            elapsed_ms=1400,
        ),
    ]

    text = render_calls(records, 1, path=Path("data/calls.jsonl"), limit=2)

    lines = text.splitlines()
    assert lines[0] == "호출 로그 data/calls.jsonl — 3건, 09-30 15:10 ~ 10-01 09:12"
    assert lines[1] == "도구별: search_docs 1 · get_decision 1 · ask 1 (MCP 2 · CLI 1)"
    assert lines[2] == "결과 없음·모름 2건 — 문서가 비어 있는 곳의 후보다"
    assert "읽지 못한 줄 1개" in text
    assert lines[-3:] == [
        "최근 2건",
        "10-01 09:12  cli  ask  모름  1.4초  6109d58  결제 PG사는?",
        "09-30 15:11  mcp  get_decision  0개  0.3초  6109d58  상품 목록 페이지 방식",
    ]


def test_render_only_empty(tmp_path):
    records = [_record("search_docs"), _record("search_docs", returned=[])]

    lines = render_calls(records, 0, path=Path("calls.jsonl"), only_empty=True).splitlines()

    assert lines[-2:] == [
        "결과 없음·모름 1건",
        "09-30 15:10  mcp  search_docs  0개  0.3초  6109d58  상품 목록 페이지 방식",
    ]


def test_render_without_calls():
    text = render_calls([], 0, path=Path("data/calls.jsonl"))

    assert text.startswith("호출 로그가 아직 없다: data/calls.jsonl")
