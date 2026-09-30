"""OpenRouter 호출 (OpenAI 호환 API). 구조화 출력(JSON 스키마)으로 받는다.

- 구조화 출력은 모델마다, 같은 모델이라도 제공자마다 지원이 달라서 `provider.require_parameters`로
  지원하는 곳으로만 보낸다 (OpenRouter 문서, implementation.md §1).
- 키는 환경 변수 OPENROUTER_API_KEY, 없으면 레포 루트 .env에서 읽는다. 키는 로그·답에 남기지 않는다.
- 재시도는 SDK의 연결 오류·429·5xx 재시도(MAX_RETRIES번)만 쓴다. 그 뒤에는 원인을 담아 멈춘다.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE_URL = "https://openrouter.ai/api/v1"
# 7단계 비교로 고른 모델 (eval/results/2026-09-30-bot-vs-baseline.md)
DEFAULT_MODEL = "google/gemini-3.5-flash-lite"
MAX_RETRIES = 2
TIMEOUT_SECONDS = 90


# 다시 불러도 같은 결과인 HTTP 상태: 요청 형식(400), 키(401), 잔액(402), 권한(403),
# 이 모델·파라미터로 갈 경로 없음(404). 429·5xx는 SDK가 MAX_RETRIES번 다시 시도한다.
FATAL_STATUS = {400, 401, 402, 403, 404}


class LLMError(RuntimeError):
    """LLM 호출이 실패했다 (키 없음, API 오류, JSON이 아닌 답).

    fatal이면 다른 질문으로 다시 불러도 같은 이유로 실패한다. 평가를 멈춰야 한다.
    """

    def __init__(self, message: str, *, fatal: bool = False) -> None:
        super().__init__(message)
        self.fatal = fatal


@dataclass(frozen=True)
class LLMResult:
    data: dict[str, Any]
    model: str  # 실제로 답한 모델 (OpenRouter가 알려 준 것)
    cost_usd: float | None
    input_tokens: int | None
    output_tokens: int | None
    duration_ms: int


def load_api_key(env_file: Path) -> str:
    key = os.environ.get("OPENROUTER_API_KEY")
    if key:
        return key
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            name, _, value = line.partition("=")
            if name.strip() == "OPENROUTER_API_KEY" and value.strip():
                key = value.strip().strip("'\"")  # 같은 이름이 여러 줄이면 마지막 줄을 쓴다
    if not key:
        raise LLMError(f"OPENROUTER_API_KEY가 없다. {env_file}에 넣는다", fatal=True)
    return key


class OpenRouter:
    def __init__(self, model: str, api_key: str) -> None:
        from openai import OpenAI  # 답변을 만들 때만 불러온다

        self.model = model
        self._client = OpenAI(
            api_key=api_key, base_url=BASE_URL, max_retries=MAX_RETRIES, timeout=TIMEOUT_SECONDS
        )

    def complete_json(self, system: str, user: str, schema: dict[str, Any], name: str) -> LLMResult:
        from openai import OpenAIError

        started = time.monotonic()
        try:
            response = self._client.chat.completions.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": name, "strict": True, "schema": schema},
                },
                extra_body={"provider": {"require_parameters": True}, "usage": {"include": True}},
            )
        except OpenAIError as e:
            status = getattr(e, "status_code", None)
            raise LLMError(
                f"{self.model} 호출 실패: {type(e).__name__}: {e}", fatal=status in FATAL_STATUS
            ) from e

        choice = response.choices[0] if response.choices else None
        content = choice.message.content if choice else None
        if not content:
            reason = choice.finish_reason if choice else "choices 없음"
            raise LLMError(f"{self.model}: 빈 답 ({reason})")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            raise LLMError(f"{self.model}: JSON이 아닌 답: {content[:200]!r}") from e
        if not isinstance(data, dict):
            raise LLMError(f"{self.model}: JSON 객체가 아니다: {content[:200]!r}")

        usage = response.usage
        extra = (usage.model_extra or {}) if usage else {}
        return LLMResult(
            data=data,
            model=response.model or self.model,
            cost_usd=extra.get("cost"),
            input_tokens=usage.prompt_tokens if usage else None,
            output_tokens=usage.completion_tokens if usage else None,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
