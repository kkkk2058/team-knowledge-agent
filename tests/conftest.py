import pytest
from helpers import make_wiki_source


@pytest.fixture
def source(tmp_path):
    """tmp_path/.cache/sources/wiki 에 만든 작은 git 저장소와 설정: (config, commit)."""
    return make_wiki_source(tmp_path)
