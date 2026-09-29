# 소스 컨텍스트

> 확인일 **2026-09-28**. 기준 커밋: wiki main `eff2eab`(2026-09-25) · FS 브랜치 `ddfba15`, BE `f32eb67`(2026-09-28). 이 날짜 이후의 변화는 반영돼 있지 않다. 판단에 쓰기 전에 오래된 항목은 다시 확인한다.
>
> **wiki main은 같은 날 `4f6a6a6`까지 나아갔다** (PR #181~#185: AI 명세·설계 문서, 결정 로그, ERD 수정 / PR #184: FS 문서 병합). 2026-09-30 확인 시 그 뒤 변화는 없다. 이 문서의 wiki 사실과 줄 번호는 아직 `eff2eab`·`ddfba15` 기준이다.
>
> 이 문서를 쓰는 **하루 사이에도** BE가 AI 챗봇 연동을 시작해 "연동 코드 없음"이 틀린 사실이 됐다(§4). 레포는 매일 바뀐다.

## 1. 서비스와 팀

- **북적북적**: 책 커머스 + AI 검색·추천 서비스.
- 팀 6명: AI 2 / 풀스택(FE·BE) 2 / 클라우드 2.
- GitHub 조직 `100-hours-a-week`. 같은 조직에 다른 KTB4 팀들의 AI·BE·FE·Cloud 레포도 있다(B안 확장 대상).
- 13기 레포 5개는 **모두 public**. 조직은 private 레포 생성을 막아 두었다(`members_can_create_private_repositories=false`).

## 2. 소스 레포

| 레포 | 스택 | 규모 | 주요 코드 위치 | 문서 위치 |
|---|---|---|---|---|
| `KTB4-13th-AI` | Python, FastAPI | 167파일, 약 2.6만 줄 | `app/routers/{search,embeddings,chat,feed,extractions,profile,agent}.py`, `app/{core,search,feed,profile,gateway,jobs,chat}/`, `db/migrations/`, `tests/` | `docs/개발-로그.md`, `docs/trouble/` (원본) · `docs/wiki/` (wiki 사본) |
| `KTB4-13th-BE` | Java, Spring Boot | 390파일 (java 301, `930370f` 기준) | `src/main/java/com/book/core/<도메인>/api/*Controller.java` 10개 (address, auth, cart, category, **onboarding**, order, product, product list, **recommendation**, review; `f32eb67`), `common/config/api/SwaggerConfig.java`, AI 호출 설정 `common/config/ai/`, `src/main/resources/ai.yml` | `.docs/ARCHITECTURE.md`, `.docs/rules/*_RULE.md`, `.docs/.conventions/`, `docs/adr/`, `AGENTS.md`, `CONTEXT.md` |
| `KTB4-13th-FE` | TypeScript(TSX), Vite | 42파일 | `src/features/auth`, `src/pages/login` (로그인 화면 수준) | `.docs/*.md` 5개, `AGENTS.md`, `README.md` |
| `KTB4-13th-CLOUD` | Shell, YAML | 33파일 | `scripts/deploy_{ai,backend,frontend}.sh`, `.github/workflows/{cd,pr-notify,release-latest}.yml`, `releases/*.yml` (자동 생성) | `README.md`, `CONTRIBUTING.md`, 레포 자체 GitHub Wiki |
| `KTB4-13th-wiki` | Markdown | 추적 md 183개 중 정리본 `docs/` 30여 개 | — | `docs/{ai,cld,dec,fs}/` = **팀 문서 원본** (`fs/`는 2026-09-28 PR #184로 main에 병합) |

## 3. 문서 복사 흐름 — 같은 문서가 여러 벌이다

```text
wiki 레포 docs/{ai,cld,dec}            ← 원본
 ├─ (wiki: sync-docs-to-ktb4-13th-ai.yml) ─▶ AI 레포 docs/wiki/
 ├─ (wiki: wiki-sync.yml) ─────────────────▶ GitHub Wiki 발행본 (KTB4-13th-wiki.wiki)
 └─ backup/wiki-original-2026-09-17, -09-21   (변환 전 스냅샷 두 벌)

AI 레포 docs/ (wiki/ 제외)              ← 원본 (개발-로그, trouble)
 └─ (AI: sync-docs-to-wiki.yml, PR 생성) ──▶ wiki 레포 docs/inbox/ai/
```

**GitHub Wiki 발행본 80페이지**의 구성은 다음과 같다.
- 변환본 29개: `AI-`·`CLD-`·`DEC-` 접두, 사이드바에 있음. wiki 레포 `docs/`와 중복.
- 특수 페이지 3개: `_Sidebar` 등.
- 사이드바 밖 옛 페이지 48개. 두 종류가 섞여 있다.
  - 과제 원본(예: `데이터-컨텍스트-보강-설계`, `모델-API-설계`): 변환본의 구버전이다.
  - 풀스택·기획 문서(예: `Backend-Wiki`, `Frontend-Wiki`, `[1단계]-테이블-명세서`, `[2단계]-API-명세서`, `Product‐Backlog`, `Sprint‐*`, `Roadmap`, `Vision`, `팀-컨벤션`).
    - **풀스택 4개는 2026-09-28에 `docs/fs/`로 변환됐다** (wiki PR #184, 2026-09-28 main에 병합 `4f6a6a6`): FS-1 테이블 명세 ← `[1단계]-테이블-명세서`, FS-2 API 명세 ← `[2단계]-API-명세서`, FS-3 기술 스택 정의 ← `[3단계]-기술-스택-정의서`, FS-4 비즈니스 정책 ← `비즈니스-정책-위키`.
    - 기획 문서는 아직 `docs/`에 대응본이 없다 (`docs/README.md` 기획 목차가 "(변환 후 추가)").

### 인덱싱 포함·제외 규칙

| 소스 | 포함 | 제외 | 단계 |
|---|---|---|---|
| wiki 레포 | `docs/README.md`, `docs/ai/`, `docs/cld/`, `docs/dec/`, `docs/fs/`, `.agents/skills/ktb4-docs/references/decisions.md` | `backup/`, `docs/inbox/`, 그 밖의 `.agents/`, `.claude/` | v0 |
| AI 레포 | `docs/` | `docs/wiki/`, `.agents/` | v1 |
| BE | `.docs/`, `docs/adr/`, `AGENTS.md`, `CONTEXT.md`, `README.md` | `.agents/` | v1 |
| FE | `.docs/`, `AGENTS.md`, `README.md` | `.agents/` | v1 |
| CLOUD | `README.md`, `CONTRIBUTING.md`, `releases/README.md`, `releases/example.yml` | `releases/v*.yml` | v1 |
| GitHub Wiki | 미정 (풀스택·기획 옛 페이지) | 변환본 29개, 과제 원본 옛 페이지 | 미정 |

- 파일 목록은 `git ls-files` 기준으로 뽑는다. 로컬 워크트리 폴더나 빌드 산출물이 섞이지 않게 하기 위해서다.
- wiki `4f6a6a6` 기준 v0 포함 파일은 **34개**다: `docs/ai/` 11, `docs/cld/` 16, `docs/dec/` 1, `docs/fs/` 4, `docs/README.md`, `decisions.md` (2026-09-30 확인). `docs/README.md`는 "변환된 문서는 이 폴더에서만 수정한다" 같은 팀 규칙이 있어서 넣는다(골든셋 g11 근거).
- `.agents/skills/`는 AI 코딩 도구용 지시문이라 원칙적으로 제외한다. 팀 지식이 아니고, 봇의 컨텍스트에 지시문이 섞일 위험이 있다. 예외로 wiki 레포의 `ktb4-docs/references/decisions.md`는 결정 로그가 팀 결정의 상세 문서로 링크하고 있어서 포함한다.

### 문서 형식에서 알아둘 것

- `docs/` 문서에는 frontmatter가 있다: `wiki`(발행 제목), `type`(design·spec·decision-log 등), `group`, `owner`, `status`, `updated`, `sources`.
  - 여기서 `status`는 **문서 작성 상태**(예: 작성중)이지 결정의 확정·폐기 여부가 아니다. 결정 상태는 결정 로그로 판단해야 한다.
- 결정 로그 `docs/dec/000-decision-log.md`는 `| 날짜 | 파트 | 결정 | 영향 파트 | 상세 |` 형식의 표다. 결정 한 줄마다 상세 문서 링크가 붙어 있어서 파싱하기 쉽다.
- **폐기 안내가 문서 상단에만 있는 경우가 있다.** 예: AI-5 문서는 13행 콜아웃에서 `book_passage` 폐기를 알리지만, §2-A·§4-A 본문은 옛 설계 서술이 "이력 참고용"으로 그대로 남아 있다. 섹션 단위로 청킹하면 본문 청크만 검색돼 폐기된 설계를 사실처럼 답할 수 있다.
- git이 한글 파일명을 8진수로 이스케이프해서 출력한다(예: `docs/\352\260\234...`). 경로를 처리할 때는 `core.quotepath=false`로 두거나 `-z` 출력을 쓴다.
- Notion에서 붙여넣은 출신의 문서에는 NBSP(U+00A0)가 섞여 있어 평범한 공백으로 문자열 매칭이 실패한 적이 있다. 인덱싱 전에 공백과 유니코드를 정규화한다.

## 4. 서비스 핵심 사실 (골든셋 재료)

근거 위치는 wiki 레포 `docs/` 기준이다.

- **AI 엔드포인트 8개**: ① `POST /search` · ② `/embeddings` · ③ `/recommendations/chat` · ④ `GET /recommendations/feed` · ⑤ `/preferences/extractions` · ⑥ `/preferences/profile` · ⑦ `/agent/act` · ⑧ `GET /health`. AI 파트 2명이 ①②④⑥ / ③⑤⑦로 나눠 맡는다. (`ai/1-model-api/spec.md`)
- **DB**: BE는 MySQL, AI는 전용 PostgreSQL + pgvector를 쓴다. BE→AI 단방향 복제로 `v_*` 복제 테이블을 만든다. 복제는 아직 구축되지 않았다(BE#65 OPEN). (`ai/3-architecture-modularization/design.md`, `ai/9-data-erd/spec.md`)
- **임베딩**: multilingual-e5-small, 384차원. (`ai/1-model-api/spec.md`)
- **① 검색**: 키워드 검색은 pg_trgm만 쓰고 형태소 분석·BM25는 V1에서 쓰지 않는다(09-24). 제목이 검색어와 같은 책을 맨 앞에 두고, 나머지는 키워드 3 : 벡터 1 가중 RRF로 합친다(09-23). (결정 로그)
- **LLM 오케스트레이션**: ③⑤는 LangChain, ⑦은 LangGraph. 09-21에 "V1 미도입" 결정을 번복했다. (결정 로그, `ai/4-multistep-pipeline/design.md`)
- **폐기**: `book_passage` 원문조각 RAG와 `preset_vectors` 얕은 RAG(09-15). `reason_long`은 `v_books.description`만 근거로 만든다. AI 소유 테이블은 `book_embeddings`·`taste_profile`·멱등 기록 3종이다. (결정 로그, `ai/5-context-augmentation/design.md`)
- **MCP**: 서비스에서는 전송 규격을 도입하지 않고, 도구 정의만 MCP 호환 형식으로 쓴다. (`ai/6-tool-integration/design.md`)
- **인기순**: 일반화 점수는 BE가 소유한다(BE#68 OPEN). AI의 `app/core/popularity.py`는 임시 공식이다.
- **풀스택 API** (FS-2): 12개 도메인, 엔드포인트 40개, 경로 접두 `/api/v1/`, 공통 응답 `{ result, data, error }`. 도메인: 인증 3 · 회원 3 · 온보딩 4 · 상품 3 · 배송지 5 · 장바구니 5 · 주문 5 · 결제 3 · 쿠폰 2 · 포인트 1 · 리뷰 4 · 알림 2. 여러 엔드포인트의 설명이 "원본에 정의되지 않음"이다. 상세 명세 원본은 Notion에 있다.
- **풀스택 테이블** (FS-1): 6개 도메인, 테이블 28개, MySQL InnoDB. 시각 컬럼은 `DATETIME(6)`(타임존 없음), 금액·포인트는 `DECIMAL`. 소프트 삭제는 `deleted_at` + `active_flag`. ERD 원본은 ERDCloud에 있다.
- **풀스택 기술 스택** (FS-3): Java 25, Gradle, Spring, MySQL(InnoDB), Redis.
- **비즈니스 정책** (FS-4): 본문에 "최종본"이라고 명시. 결제는 테스트 운영, 배송비 0원, 실제 배송·회수·정산 제외, 탈퇴 신청 후 7일 복구 가능, 포인트 1점 = 1원. 원본 체크리스트 126개 대응표 포함.
- **BE↔AI 연동**: 2026-09-28 BE `f32eb67`에서 ③ 챗봇 연동이 시작됐다.
  - BE `core/recommendation/`: `RecommendationController`가 `@RequestMapping("/api/v1/recommend")` 아래 `POST /chat`, `GET /cards/{recommendationCardId}`를 연다.
  - `infrastructure/client/ai/AiRecommendationClientImpl`이 AI `/recommendations/chat`을 호출한다. 설정은 `ai.yml`(`ai.base-url`, `ai.service-token`).
  - AI 응답 봉투는 `AiChatEnvelope`가 `data`만 읽고 나머지 필드는 무시한다.
  - 추천 카드는 BE가 저장한다(`V19__create_recommendation_cards.sql`) — AI 명세의 "`reason_long`은 BE가 보관"과 맞는 방향이다.
  - ①②④⑥⑦ 연동은 파일 경로 기준으로 아직 보이지 않는다.
  - AI `app/main.py`에는 "BE 연동을 시작하면 이 블록과 dev/ 폴더를 함께 삭제한다"는 주석이 아직 있다(2026-09-28, 로컬 작업 브랜치 기준).

## 5. 결정이 뒤집힌 이력 — 함정 질문 재료

옛 서술은 `backup/`, GitHub Wiki 옛 페이지, 문서 본문의 "이력 참고용" 절에 아직 남아 있다. 봇이 이걸 현재 사실로 답하는지가 핵심 평가 항목이다.

| 주제 | 이전 | 현재 | 날짜 |
|---|---|---|---|
| LLM 프레임워크 | LangChain 미도입 | ③⑤ LangChain, ⑦ LangGraph | 09-21 |
| ① 키워드 검색 | BM25 + tsvector | pg_trgm만 사용, tsvector 색인 삭제 | 09-24 |
| 원문조각 RAG | 채택으로 서술 | 폐기 | 09-15 |
| 임베딩 모델 | bge-m3, 1024차원 전제 | e5-small, 384차원 | 09-14 전후 |
| ④ feed 메서드 | POST | GET | 09-16 |
| 추천 이유 상세 | 별도 엔드포인트 `/books/{id}/match-reason` | 삭제, ③ 카드의 `reason_long`으로 편입 | 09-08 |
| BE·AI DB | Postgres 공유 | 분리 + 단방향 복제 | 09-08~09 |

## 5-1. 문서끼리·문서와 코드가 어긋난 사례 — 드리프트 리포트 검증용

2026-09-28에 사람이 직접 대조해서 찾은 것들이다. 구조화된 버전은 [eval/drift_cases.yaml](../eval/drift_cases.yaml)에 있다. 드리프트 리포트 기능을 만들면 **이것들을 스스로 찾아내는지**로 검증한다. 각 항목이 실제 문제인지(의도된 차이인지)는 해당 파트에 확인해야 한다.

| # | 어긋남 | 근거 |
|---|---|---|
| 1 | 공통 응답 형식: 결정 로그 09-17은 "FS·AI 공통 `{message, data}`"인데, FS-2는 `{ result, data, error }`. 결정 로그에 형식 변경 기록이 없다 | `dec/000-decision-log.md` 09-17 행, `fs/2-api/spec.md` "공통 응답" |
| 2 | 검색·추천·챗봇 경로: AI 명세는 ①③④를 "BE가 호출한다"고 하는데, FS-2 엔드포인트 40개에는 검색·추천·챗봇이 없다. 반면 BE 코드(`f32eb67`)에는 `POST /api/v1/recommend/chat`, `GET /api/v1/recommend/cards/{id}`가 있다 → "코드에는 있는데 명세에는 없는 API" | `ai/1-model-api/spec.md` §1, `fs/2-api/spec.md`, BE `core/recommendation/api/RecommendationController.java` |
| 3 | 배송: AI V2 tool에 `delivery.track`(배송 조회)이 있는데, FS-4는 실제 배송을 범위에서 제외하고 FS-2에 배송 엔드포인트가 없다 | `ai/1-model-api/spec.md` tool 표, `fs/4-business-policy/spec.md` §1 |
| 4 | 명세 대비 구현: FS-2 도메인은 12개, BE 컨트롤러는 `f32eb67` 기준 10개(onboarding, recommendation 포함). 회원·결제·쿠폰·포인트·알림 컨트롤러 파일이 없고, 반대로 recommendation은 FS-2에 없다 (파일명 기준, 다른 컨트롤러에 들어 있을 수 있음. 개발 진행 중이라 미구현일 수 있음) | `fs/2-api/spec.md`, BE `src/main/java/com/book/core/*/api/` |
| 5 | 같은 문서 안 모순: FS-1에서 `active_flag`를 한 곳은 STORED, 다른 곳은 VIRTUAL 생성 컬럼이라고 한다 | `fs/1-table-spec/spec.md` 22행 · 55행 |
| 6 | 상태 표기: FS-4 frontmatter `status: 작성중`, 본문은 "최종본" | `fs/4-business-policy/spec.md` |
| 7 | 같은 문서 안 요약 누락 (후보): AI ERD 19행 "BE 복제" 요약에는 `v_books`, `v_book_popularity`, `v_user_*` 3종만 있고 `v_products`가 없다. 68행 표와 §3.11은 `v_products`를 정의한다(09-25 결정, AI #208) | `ai/9-data-erd/spec.md` 19행 · 68행 · 262행 |
| 8 | AI 문서와 변환 전 클라우드 원본: AI 문서는 V2 ⑤ 취향 추출을 야간 배치로 두고 메시지 큐는 선택지로 열어 뒀는데, 클라우드 위키 원본 "AI 작업 큐 도입 판단"은 V2 AI 작업 큐를 SQS Standard + DLQ로 확정했다(2026-09-22 타운홀 wiki #138에서도 보고). 원본이 아직 `docs/`로 변환되지 않아 `backup/`에만 있다. ⑤만 배치로 남기는 의도된 차이일 수 있다 (2026-09-30 확인, `4f6a6a6`) | `ai/3-architecture-modularization/design.md` 91행, `backup/wiki-original-2026-09-21/2.4.-AI-연동-구조의-한계와-개선.md` 5·388행 |

모순처럼 보였지만 아닌 사례(BE 시각 컬럼 해석 시간대)는 [eval/drift_cases.yaml](../eval/drift_cases.yaml)의 `withdrawn`에 있다. 모순 리포트가 이런 오탐을 내지 않는지(정밀도) 확인하는 데 쓴다.

## 6. 골든셋

[eval/golden.yaml](../eval/golden.yaml)에 있다. v0 20문항, wiki `4f6a6a6` 기준 줄 번호 (2026-09-30).
유형: 명세 값 · 함정 · 결정 이유 · 장애 동작 · 클라우드 · 팀 규칙 · 정책 값 · 문서끼리 다름 · 모름 · 회의 결정.

- 검사: `uv run python -m tka.golden check` — 근거 줄이 기준 커밋에 있는지, v0 근거가 포함 규칙 안인지, 모름 문항의 단어가 정말 문서에 없는지.
- 확인 시트: `uv run python -m tka.golden review` — 근거 줄 원문을 붙인 시트를 `data/`에 만든다(커밋하지 않음).

## 7. 아직 확인하지 않은 것

- CLOUD 레포 자체 GitHub Wiki와 wiki 레포 `docs/cld/`의 관계 (같은 내용의 원본과 변환본인지).
- BE의 ①②④⑥⑦ 연동 여부, BE가 AI에 보내는 요청 필드가 AI 명세와 맞는지 (③ 챗봇 연동만 확인, §4).
- wiki main `4f6a6a6`에서 바뀐 내용이 §4·§5의 사실과 줄 번호에 주는 영향 (골든셋 §6은 `4f6a6a6`로 다시 맞췄다).
- FS 문서의 원본이 Notion(API)·ERDCloud(ERD)에도 있는데, wiki 변환본과 어느 쪽이 최신인지.
