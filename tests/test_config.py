from pathlib import Path

import pytest
import yaml

from tka.config import COMMIT_PATTERN, ConfigError, load_config

TEAM_CONFIG = Path(__file__).parent.parent / "config" / "ktb13.yaml"


def _valid() -> dict:
    return {
        "team": "t",
        "decision_log_year": 2026,
        "paths": {"cache_dir": ".cache/sources", "index_path": "data/index.sqlite"},
        "sources": [
            {"name": "wiki", "repo": "org/wiki", "ref": "4f6a6a6", "include": ["docs/"]},
        ],
    }


def _write(tmp_path: Path, data: dict) -> Path:
    path = tmp_path / "c.yaml"
    path.write_text(yaml.safe_dump(data, allow_unicode=True), encoding="utf-8")
    return path


def test_team_config_loads_with_pinned_commit():
    config = load_config(TEAM_CONFIG)

    assert [s.name for s in config.sources] == ["wiki"]
    wiki = config.sources[0]
    assert wiki.repo == "100-hours-a-week/KTB4-13th-wiki"
    assert COMMIT_PATTERN.fullmatch(wiki.ref)
    assert "docs/ai/" in wiki.include
    assert wiki.extensions == (".md",)
    assert config.decision_log_year == 2026


def test_optional_lists_default(tmp_path):
    source = load_config(_write(tmp_path, _valid())).sources[0]

    assert source.exclude == ()
    assert source.extensions == (".md",)


@pytest.mark.parametrize("ref", ["main", "docs/fs-1-4-conversion", "4f6a6a", "HEAD"])
def test_branch_or_short_ref_rejected(tmp_path, ref):
    data = _valid()
    data["sources"][0]["ref"] = ref

    with pytest.raises(ConfigError, match=r"sources\[0\]\.ref: 브랜치 이름이 아니라 커밋"):
        load_config(_write(tmp_path, data))


def test_all_digit_commit_accepted(tmp_path):
    data = _valid()
    data["sources"][0]["ref"] = 1234567  # YAML이 숫자로 읽는 커밋

    assert load_config(_write(tmp_path, data)).sources[0].ref == "1234567"


def test_missing_field_names_its_location(tmp_path):
    data = _valid()
    del data["sources"][0]["repo"]

    with pytest.raises(ConfigError, match=r"sources\[0\]\.repo: 없다"):
        load_config(_write(tmp_path, data))


def test_wrong_type_is_distinguished_from_missing(tmp_path):
    data = _valid()
    data["decision_log_year"] = "2026"

    with pytest.raises(ConfigError, match=r"decision_log_year: int 타입이어야 한다"):
        load_config(_write(tmp_path, data))


def test_bool_is_not_accepted_as_int(tmp_path):
    data = _valid()
    data["decision_log_year"] = True

    with pytest.raises(ConfigError, match="decision_log_year: int 타입이어야 한다"):
        load_config(_write(tmp_path, data))


def test_empty_include_rejected(tmp_path):
    data = _valid()
    data["sources"][0]["include"] = []

    with pytest.raises(ConfigError, match=r"sources\[0\]\.include: 비어 있다"):
        load_config(_write(tmp_path, data))


def test_duplicate_source_names_rejected(tmp_path):
    data = _valid()
    data["sources"].append(dict(data["sources"][0]))

    with pytest.raises(ConfigError, match="이름이 겹친다: wiki"):
        load_config(_write(tmp_path, data))


def test_yaml_syntax_error_is_config_error(tmp_path):
    path = tmp_path / "c.yaml"
    path.write_text("team: [unclosed\n", encoding="utf-8")

    with pytest.raises(ConfigError, match="YAML 문법 오류"):
        load_config(path)


def test_missing_file_is_not_config_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "없음.yaml")
