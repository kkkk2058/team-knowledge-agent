# team-knowledge-agent

북적북적 팀(KTB4-13th) 레포의 문서를 근거로 팀 질문에 답하는 팀 전용 지식 에이전트.

- 답마다 근거 위치(`레포/경로:줄@커밋`)를 붙이고, 근거가 없으면 "모름"이라고 답한다.
- 결정이 뒤집힌 이력을 구분해 **지금 기준**으로 답한다.
- 다음 단계에서는 문서끼리, 문서와 코드가 안 맞는 곳을 찾는다.

## v0 범위

| 항목 | v0 |
|---|---|
| 소스 | wiki 레포 `docs/` ([config/ktb13.yaml](config/ktb13.yaml)) |
| 기준 커밋 | wiki `4f6a6a6`. 평가는 브랜치가 아니라 고정 커밋으로 한다 |
| 입구 | CLI 하나 |
| 평가 | 골든셋 20문항(튜닝용), 베이스라인 Claude Code 새 세션. 최종 비교는 따로 모은 채점용 질문으로 |

v0에서 하지 않는 것: 코드 읽기, 다른 레포 문서, 이슈·PR, 쓰기 행동. 단계별 계획은 [docs/plan.md](docs/plan.md) §4, 구현 순서는 [docs/implementation.md](docs/implementation.md) §4.

## 개발

```bash
uv sync
uv run pytest
uv run ruff check
```

- 소스 레포 캐시(`.cache/`)와 인덱스(`data/`)는 커밋하지 않는다. 소스 문서는 항상 원본 레포에서 가져온다.
- `.env`에는 OpenRouter 키(`OPENROUTER_API_KEY`)를 넣는다 (답변 단계부터 필요). 커밋하지 않는다.

### 소스 가져오기

```bash
uv run python -m tka.ingest fetch    # 설정의 소스 레포를 .cache/sources/에 받아 기준 커밋으로 맞춘다
uv run python -m tka.ingest files    # 포함 규칙으로 고른 파일 수 (wiki 4f6a6a6: 34개). --list로 목록
uv run python -m tka.ingest chunks   # 청크로 잘라 data/chunks.jsonl에 덤프 (wiki 4f6a6a6: 720개). --list로 청크 제목
```

캐시에 로컬 수정이 있으면 덮어쓰지 않고 멈춘다. 캐시 폴더를 지우고 다시 받으면 된다.

### 결정 표와 MCP

```bash
uv run python -m tka.decisions check              # 결정 표 행 수 = 로그 행 수, 링크·보정 검사
uv run python -m tka.decisions find "LangChain"   # get_decision과 같은 결과를 터미널에서
```

본인 Claude Code에 연결한다 (user 범위, 모든 레포에서 쓴다). 도구는 `get_decision`(결정 로그)과 `search_docs`(문서 검색) 두 개다. 서버는 최신 wiki main을 따라가고 호출을 `data/mcp_calls.jsonl`에 남긴다.

개발 중인 브랜치가 봇을 깨지 않게, 봇은 main만 따르는 작업 폴더(worktree)에서 돌린다. PR을 병합하면 `git -C <live 폴더> pull`로 갱신한다.

```bash
git -C /path/to/team-knowledge-agent worktree add --track -b live /path/to/team-knowledge-agent-live origin/main
claude mcp add --scope user tka -- uv run --directory /path/to/team-knowledge-agent-live python -m tka.mcp_server
```

### 검색

```bash
uv run python -m tka.retrieve search "탈퇴하면 며칠 안에 복구돼?"   # 기본 방식: RRF 3:1 · Kiwi · 청크 900자
uv run python -m tka.retrieve eval                                  # 방식별 recall@5 → eval/results/<날짜>-retrieval/
```

처음 한 번은 e5-small 모델을 내려받고 청크를 임베딩한다(1분 안팎). 임베딩은 `data/index.sqlite`에 캐시한다.

### 답변

```bash
uv run python -m tka.answer ask "① 검색에 BM25를 쓰나?"          # 기준 커밋 문서로 답하고 근거를 붙인다
uv run python -m tka.answer eval --model google/gemini-3.5-flash-lite   # 골든셋 v0 → eval/results/<날짜>-bot-<모델>/
```

`.env`에 `OPENROUTER_API_KEY`가 필요하다. 모델 기본값은 google/gemini-3.5-flash-lite다. 문장마다 근거가 붙고, 인용(`레포/경로:줄@커밋`)은 LLM이 아니라 코드가 만든다.

튜닝용 20문항 결과 (2026-09-30, 채점은 레포 주인 확인 대기): 봇 90%(18/20), 베이스라인 Claude Code 92%(18.5/20). 봇은 질문당 $0.0009·1.3초, 베이스라인은 $0.127·21.5초. 자세한 비교는 [eval/results/2026-09-30-bot-vs-baseline.md](eval/results/2026-09-30-bot-vs-baseline.md).

### 골든셋 검사

```bash
uv run python -m tka.golden check    # 근거 줄·포함 규칙·모름 문항 검사
uv run python -m tka.golden review   # 정답 확인 시트 → data/golden_review.md
```

### 베이스라인과 채점

모든 시스템에 같은 질문 형식을 쓴다 (`tka.evaluation.PROMPT_TEMPLATE`). 결과는 `eval/results/<날짜>-<시스템>/`에 `answers.yaml`(답), `scores.yaml`(정답·부분·오답), `summary.md`(요약)로 남긴다.

```bash
# Claude Code: 이 프로젝트 밖에 깨끗한 clone을 두고 새 세션으로 묻는다 (상위 폴더 CLAUDE.md가 섞이지 않게)
git clone https://github.com/100-hours-a-week/KTB4-13th-wiki /tmp/tka-baseline/KTB4-13th-wiki
git -C /tmp/tka-baseline/KTB4-13th-wiki checkout 4f6a6a6
uv run python -m tka.baseline --repo-dir /tmp/tka-baseline/KTB4-13th-wiki

# 손으로 묻는 시스템(예: DeepWiki, 선택): 질문이 적힌 틀을 만들고 답을 붙여 넣는다
uv run python -m tka.evaluation template eval/results/<날짜>-deepwiki --system deepwiki

# scores.yaml을 채운 뒤 요약
uv run python -m tka.evaluation summary eval/results/<run>
```

## 문서

작업 규칙과 문서 안내는 [CLAUDE.md](CLAUDE.md)에 있다.
