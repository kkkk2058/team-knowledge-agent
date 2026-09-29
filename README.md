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
| 평가 | 골든셋 20문항, 베이스라인 2개 (Claude Code, DeepWiki) |

v0에서 하지 않는 것: 코드 읽기, 다른 레포 문서, 이슈·PR, 쓰기 행동. 단계별 계획은 [docs/plan.md](docs/plan.md) §4, 구현 순서는 [docs/implementation.md](docs/implementation.md) §4.

## 개발

```bash
uv sync
uv run pytest
uv run ruff check
```

- 소스 레포 캐시(`.cache/`)와 인덱스(`data/`)는 커밋하지 않는다. 소스 문서는 항상 원본 레포에서 가져온다.

### 골든셋 검사

소스를 기준 커밋으로 받아 둔 뒤 검사한다. 가져오기 명령은 3단계에서 생긴다.

```bash
git clone https://github.com/100-hours-a-week/KTB4-13th-wiki .cache/sources/wiki
git -C .cache/sources/wiki checkout 4f6a6a6
uv run python -m tka.golden check    # 근거 줄·포함 규칙·모름 문항 검사
uv run python -m tka.golden review   # 정답 확인 시트 → data/golden_review.md
```
- `.env`에는 LLM 키를 넣는다 (답변 단계부터 필요). 커밋하지 않는다.

## 문서

작업 규칙과 문서 안내는 [CLAUDE.md](CLAUDE.md)에 있다.
