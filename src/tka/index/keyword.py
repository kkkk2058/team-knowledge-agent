"""키워드 검색: 토크나이저 세 가지와 BM25 (implementation.md ④).

한국어는 조사가 붙어 공백 나누기로는 "피드는"과 "피드"가 다른 말이 된다. 그래서 비교한다.
- words: 공백·문장부호로 나눈다 (기준선)
- ngram: 낱말을 글자 2·3개 조각으로 나눈다. 서비스가 쓰는 pg_trgm 계열이다. 3글자 조각만 쓰면
  "배송", "결제" 같은 두 글자 검색어가 매칭되지 않으므로(SQLite FTS5 트라이그램과 같은 함정)
  2글자도 넣는다.
- kiwi: Kiwi 형태소 분석으로 명사·동사·영문·숫자만 남긴다. 조사·어미는 버린다.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from collections.abc import Callable
from functools import lru_cache

import numpy as np

Tokenizer = Callable[[str], list[str]]

_WORD = re.compile(r"\w+")
# Kiwi 품사 중 남길 것: 명사(N…), 동사·형용사 어간(VV·VA), 어근(XR), 영문(SL)·숫자(SN)·한자(SH)
_KEEP_TAGS = re.compile(r"^(N|VV|VA|XR|SL|SN|SH)")


def tokenize_words(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def tokenize_ngrams(text: str) -> list[str]:
    tokens = []
    for word in _WORD.findall(text.lower()):
        if len(word) <= 2:
            tokens.append(word)
            continue
        for n in (2, 3):
            tokens.extend(word[i : i + n] for i in range(len(word) - n + 1))
    return tokens


def tokenize_kiwi(text: str) -> list[str]:
    return [
        t.form.lower() for t in _kiwi().tokenize(text) if _KEEP_TAGS.match(t.tag) and t.form.strip()
    ]


@lru_cache(maxsize=1)
def _kiwi():
    from kiwipiepy import Kiwi  # 불러오는 데 1초쯤 걸려 쓸 때만 연다

    return Kiwi()


TOKENIZERS: dict[str, Tokenizer] = {
    "words": tokenize_words,
    "ngram": tokenize_ngrams,
    "kiwi": tokenize_kiwi,
}


class BM25:
    """Okapi BM25. 역색인으로 검색어가 든 문서만 계산한다."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.n = len(docs)
        self.k1, self.b = k1, b
        self.lengths = np.array([len(d) for d in docs], dtype=float)
        self.avg_length = float(self.lengths.mean()) if self.n else 0.0
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, doc in enumerate(docs):
            for term, tf in Counter(doc).items():
                self.postings[term].append((i, tf))
        self.idf = {
            term: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5))
            for term, p in self.postings.items()
        }

    def scores(self, query: list[str]) -> np.ndarray:
        scores = np.zeros(self.n)
        for term in set(query):
            idf = self.idf.get(term)
            if idf is None:
                continue
            for i, tf in self.postings[term]:
                norm = self.k1 * (1 - self.b + self.b * self.lengths[i] / self.avg_length)
                scores[i] += idf * tf * (self.k1 + 1) / (tf + norm)
        return scores
