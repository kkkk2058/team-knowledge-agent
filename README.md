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
| 입구 | `tka` 명령, 본인 Claude Code의 MCP 도구 |
| 평가 | 골든셋 20문항(튜닝용), 베이스라인 Claude Code 새 세션. 최종 비교는 따로 모은 채점용 질문으로 |

v0에서 하지 않는 것: 코드 읽기, 다른 레포 문서, 이슈·PR, 쓰기 행동. 단계별 계획은 [docs/plan.md](docs/plan.md) §4, 구현 순서는 [docs/implementation.md](docs/implementation.md) §4.

## 쓰기

봇은 main만 따르는 작업 폴더(live)에서 돌린다. 개발 중인 브랜치가 봇을 깨지 않게 하려는 것이다. PR을 병합하면 `git -C <live 폴더> pull`로 갱신한다.

```bash
git -C /path/to/team-knowledge-agent worktree add --track -b live /path/to/team-knowledge-agent-live origin/main
alias tka='uv run --directory /path/to/team-knowledge-agent-live tka'
```

```bash
tka decision "feed 메서드"              # 결정 로그의 결정 (상태·이전 결정·인용)
tka search "상품 목록 API 페이지 방식"    # 문서 조각과 인용
tka ask "홈 피드는 POST로 부르나?"       # 문서를 근거로 답한다 (LLM, 질문당 약 $0.001)
tka log                                 # 호출 로그 (MCP와 tka). --empty면 결과 없음·모름만
```

- 기본은 최신 wiki main을 따른다(10분마다 확인). `--pinned`면 평가 기준 커밋(`4f6a6a6`)이다.
- `ask`는 live 폴더의 `.env`나 환경 변수에 `OPENROUTER_API_KEY`가 필요하다. 모델 기본값은 google/gemini-3.5-flash-lite다. 문장마다 근거가 붙고, 인용(`레포/경로:줄@커밋`)은 LLM이 아니라 코드가 만든다.
- 처음 한 번은 e5-small 모델을 내려받고 청크를 임베딩한다(1분 안팎). 그 뒤 `search`·`ask`는 모델을 불러오느라 10초 안팎 걸린다.
- 호출은 `data/calls.jsonl`에 남는다(커밋하지 않음). 틀린 답과 "모름"은 골든셋 재료다.

### Claude Code에 연결 (MCP)

```bash
claude mcp add --scope user tka -- uv run --directory /path/to/team-knowledge-agent-live python -m tka.mcp_server
```

도구는 `get_decision`(결정 로그)과 `search_docs`(문서 검색) 두 개이고, 답이 아니라 근거를 돌려준다(답은 Claude Code가 쓴다). 도구 설명만으로는 잘 부르지 않으므로 `~/.claude/CLAUDE.md`에 언제 부를지 한 줄 적는다(plan.md §1-1). 예:

```text
KTB4-13th 레포에서 다른 파트의 API·명세 필드·팀 결정은 추측하지 말고 tka MCP 도구(get_decision, search_docs)로 확인한다.
```

튜닝용 20문항 결과 (2026-09-30): 봇 90%(18/20), 베이스라인 Claude Code 92%(18.5/20). 봇은 질문당 $0.0009·1.3초, 베이스라인은 $0.127·21.5초. 자세한 비교는 [eval/results/2026-09-30-bot-vs-baseline.md](eval/results/2026-09-30-bot-vs-baseline.md).

## 개발

```bash
uv sync
uv run pytest
uv run ruff check
```

- 소스 레포 캐시(`.cache/`)와 인덱스(`data/`)는 커밋하지 않는다. 소스 문서는 항상 원본 레포에서 가져온다.
- `.env`에는 OpenRouter 키(`OPENROUTER_API_KEY`)를 넣는다 (답변 단계부터 필요). 커밋하지 않는다.
- 아래 명령은 개발·평가용이고 설정의 기준 커밋 문서로 돈다. 사용자 명령은 위의 `tka`다.

### 소스 가져오기

```bash
uv run python -m tka.ingest fetch    # 설정의 소스 레포를 .cache/sources/에 받아 기준 커밋으로 맞춘다
uv run python -m tka.ingest files    # 포함 규칙으로 고른 파일 수 (wiki 4f6a6a6: 34개). --list로 목록
uv run python -m tka.ingest chunks   # 청크로 잘라 data/chunks.jsonl에 덤프 (wiki 4f6a6a6: 720개). --list로 청크 제목
```

캐시에 로컬 수정이 있으면 덮어쓰지 않고 멈춘다. 캐시 폴더를 지우고 다시 받으면 된다.

### 결정 표·검색·답변 측정

```bash
uv run python -m tka.decisions check                                   # 결정 표 행 수 = 로그 행 수, 링크·보정 검사
uv run python -m tka.retrieve eval                                     # 방식별 recall@5 → eval/results/<날짜>-retrieval/
uv run python -m tka.answer eval --model google/gemini-3.5-flash-lite  # 골든셋 v0 답 → eval/results/<날짜>-bot-<모델>/
```

임베딩은 `data/index.sqlite`에 캐시한다. `answer eval`은 호출 로그를 남기지 않는다.

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

### API 대조표

명세 ↔ 서버 코드 ↔ 호출 코드(FE→BE, BE→AI)를 LLM 없이 맞춘다. 소스는 `config/ktb13.yaml`의 `contracts.sources`(네 레포를 같은 날 main으로 고정)를 따로 받는다.

```bash
uv run python -m tka.contracts fetch
uv run python -m tka.contracts check --out eval/results/<날짜>-contracts/report.md
```

명세에만 있는 것, 코드에만 있는 것, 메서드만 다른 것, 서버에 없는 경로를 부르는 호출, 아무도 안 부르는 서버 엔드포인트를 보여준다. 의도된 차이인지는 사람이 판단한다.

## 문서

작업 규칙과 문서 안내는 [CLAUDE.md](CLAUDE.md)에 있다.
