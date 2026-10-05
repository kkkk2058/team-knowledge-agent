import random

import pytest
from helpers import decision_config, make_decision_repo

from tka import bigset
from tka.answer.llm import LLMError, LLMResult
from tka.bigset import (
    Doc,
    Window,
    assign_splits,
    baseline_sample,
    decision_items,
    drop_duplicates,
    generate,
    heading_path,
    passage_item,
    pick_windows,
    unknown_items,
)
from tka.decisions.table import build_table
from tka.evaluation import dump_yaml
from tka.golden import load_golden


def _doc(n_lines=100, path="docs/a.md", text="내용 {n}") -> Doc:
    lines = ["---", "wiki: 문서 A", "---", "# 제목"]
    lines += ["" if n % 5 == 0 else text.format(n=n) for n in range(5, n_lines + 1)]
    return Doc(path, "문서 A", tuple(lines), 4)


def test_frontmatter_title_and_body_start():
    assert bigset._frontmatter(["---", "wiki: AI-1 명세", "type: spec", "---", "# x"]) == (
        5,
        "AI-1 명세",
    )
    assert bigset._frontmatter(["# 제목만"]) == (1, None)


def test_windows_do_not_overlap_and_skip_old_text(monkeypatch):
    monkeypatch.setattr(bigset, "CHARS_PER_QUESTION", 100)
    doc = _doc(300)

    windows = pick_windows([doc], random.Random(1), avoid={"docs/a.md": set(range(1, 150))})

    assert 2 <= len(windows) <= bigset.MAX_PER_DOC
    assert all(w.start >= 150 for w in windows)  # 옛 서술 줄이 든 창은 뺀다
    spans = sorted((w.start, w.end) for w in windows)
    assert all(a[1] < b[0] for a, b in zip(spans, spans[1:], strict=False))
    again = pick_windows([doc], random.Random(1), avoid={"docs/a.md": set(range(1, 150))})
    assert [(w.start, w.end) for w in again] == [(w.start, w.end) for w in windows]  # 같은 씨앗


def test_heading_path_ignores_code_blocks():
    doc = Doc("d.md", "D", ("# 가", "## 나", "```", "# 코드 주석", "```", "### 다", "본문"), 1)

    assert heading_path(doc, 7) == ["가", "나", "다"]


def _window() -> Window:
    return Window(_doc(60), 10, 39)


def _data(**kw):
    base = {
        "usable": True,
        "type": "명세 값",
        "question": "임베딩 모델 뭐 써?",
        "expected": "e5-small",
        "evidence_start": 12,
        "evidence_end": 13,
    }
    base.update(kw)
    return base


@pytest.mark.parametrize(
    "data",
    [
        _data(usable=False),
        _data(evidence_start=5, evidence_end=6),  # 창 밖
        _data(evidence_start=12, evidence_end=30),  # 10줄 넘음
        _data(evidence_start=15, evidence_end=15),  # 빈 줄
        _data(question=" "),
        _data(type="함정"),  # 구절 질문 유형이 아니다
    ],
)
def test_passage_item_rejects_bad_answers(data):
    assert passage_item(_window(), data) is None


def test_passage_item_keeps_checked_evidence():
    item = passage_item(_window(), _data())

    assert item == {
        "type": "명세 값",
        "question": "임베딩 모델 뭐 써?",
        "expected": "e5-small",
        "evidence": [{"path": "docs/a.md", "lines": "12-13"}],
    }


@pytest.fixture
def table(tmp_path):
    commit = make_decision_repo(tmp_path / ".cache" / "sources" / "wiki")
    config = decision_config(tmp_path, commit)
    return config, build_table(config, tmp_path)


def test_decision_items_mark_traps_and_skip_unknown_lines(table):
    _, t = table
    data = {
        "questions": [
            {"line": 8, "question": "피드는 뭘로 불러?"},
            {"line": 9, "question": "LangChain 써?"},
            {"line": 9, "question": "중복"},
            {"line": 99, "question": "없는 줄"},
        ]
    }

    items = decision_items(t, data)

    assert [(i["type"], i["evidence"][0]["lines"]) for i in items] == [
        ("현행 결정", "8"),
        ("함정", "9"),  # 보정 파일에 이전 결정·옛 서술이 있다
    ]
    assert items[1]["expected"].startswith("③⑤ LangChain 도입")


def test_unknown_items_drop_terms_found_in_docs():
    docs = [_doc(20, text="토스페이먼츠 연동 {n}")]
    data = {
        "questions": [
            {"question": "PG사 어디?", "absent_terms": ["토스페이먼츠", "PortOne"]},
            {"question": "FE 상태 관리?", "absent_terms": ["Zustand", "Redux"]},
            {"question": "단어 하나", "absent_terms": ["Recoil"]},
        ]
    }

    items = unknown_items(docs, data)

    assert [i["question"] for i in items] == ["FE 상태 관리?"]
    assert items[0]["type"] == "모름" and items[0]["evidence"] == []


def test_drop_duplicates_by_evidence_and_wording():
    def item(q, lines):
        return {"question": q, "evidence": [{"path": "a.md", "lines": lines}]}

    items = [
        item("상품 목록 API 페이지 방식은?", "1"),
        item("상품 목록 API 페이지 방식은 뭐야?", "5"),  # 같은 말
        item("배송비는 얼마야?", "1"),  # 같은 근거
        item("탈퇴 후 복구 기간은?", "9"),
    ]

    assert [i["question"] for i in drop_duplicates(items)] == [
        "상품 목록 API 페이지 방식은?",
        "탈퇴 후 복구 기간은?",
    ]


def test_splits_are_balanced_per_type_and_baseline_takes_half_each():
    items = [{"id": f"b{n:03d}", "type": "명세 값" if n < 10 else "모름"} for n in range(16)]

    assign_splits(items, random.Random(3))
    picked = baseline_sample(items, random.Random(3), 6)

    specs = [i["split"] for i in items if i["type"] == "명세 값"]
    assert specs.count("dev") == specs.count("test") == 5
    by_id = {i["id"]: i["split"] for i in items}
    assert sorted(by_id[i] for i in picked) == ["dev"] * 3 + ["test"] * 3


class FakeLLM:
    model = "fake/gen"

    def complete_json(self, system, user, schema, name):
        if name == "item":
            first = int(user.split("[구절] (왼쪽 숫자가 줄 번호)\n")[1].split()[0])
            data = {
                "usable": True,
                "type": "명세 값",
                "question": f"{first}번째 줄 내용은 무엇이야?",
                "expected": "답",
                "evidence_start": first,
                "evidence_end": first,
            }
        elif name == "d":
            data = {"questions": [{"line": 8, "question": "피드 호출 방식?"}]}
        else:
            data = {"questions": [{"question": "PG사?", "absent_terms": ["PortOne", "Toss"]}]}
        return LLMResult(data, self.model, 0.001, 10, 10, 1)


def test_generate_writes_a_loadable_golden_file(tmp_path, table):
    config, t = table

    data = generate(config, tmp_path, FakeLLM(), progress=lambda _: None)
    path = tmp_path / "bigset.yaml"
    path.write_text(dump_yaml(data), encoding="utf-8")
    golden = load_golden(path)

    types = {i.type for i in golden.items}
    assert {"명세 값", "현행 결정", "모름"} <= types
    assert all(i.status == "자동 생성" and i.split in ("dev", "test") for i in golden.items)
    assert golden.source_commits == {"wiki": t.commit[:7]}
    assert data["meta"]["generated_by"] == "fake/gen"
    assert set(data["meta"]["baseline_ids"]) <= {i.id for i in golden.items}
    # 결정 로그는 구절로 뽑지 않는다 (결정 질문으로 따로 만든다)
    assert not any(
        e.path == t.log_path for i in golden.items if i.type == "명세 값" for e in i.evidence
    )


def test_generate_stops_on_fatal_error(tmp_path, table):
    config, _ = table

    class Broken(FakeLLM):
        def complete_json(self, *args):
            raise LLMError("404", fatal=True)

    with pytest.raises(LLMError, match="404"):
        generate(config, tmp_path, Broken(), progress=lambda _: None)
