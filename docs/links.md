# 링크 모음

> 2026-09-28 기준. 조직 = `100-hours-a-week`. 소스 레포는 모두 public이다.

## 1. 소스 레포

| 레포 | 링크 | 기본 브랜치 | 기준 커밋 (2026-09-28) |
|---|---|---|---|
| AI | https://github.com/100-hours-a-week/KTB4-13th-AI | main | — |
| BE | https://github.com/100-hours-a-week/KTB4-13th-BE | main | `f32eb67` (전날 `930370f`) |
| FE | https://github.com/100-hours-a-week/KTB4-13th-FE | main | — |
| CLOUD | https://github.com/100-hours-a-week/KTB4-13th-CLOUD | main | — |
| wiki | https://github.com/100-hours-a-week/KTB4-13th-wiki | main | main `eff2eab` · FS 브랜치 `ddfba15` |

- FS 문서 브랜치: https://github.com/100-hours-a-week/KTB4-13th-wiki/tree/docs/fs-1-4-conversion
- GitHub Wiki 발행본: https://github.com/100-hours-a-week/KTB4-13th-wiki/wiki (clone: `https://github.com/100-hours-a-week/KTB4-13th-wiki.wiki.git`)
- CLOUD 레포 자체 GitHub Wiki: https://github.com/100-hours-a-week/KTB4-13th-CLOUD/wiki (wiki 레포 `docs/cld/`와의 관계 미확인)

## 2. 핵심 문서 (wiki 레포 원본)

| 문서 | 링크 |
|---|---|
| 문서 목차 | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/docs/README.md |
| DEC-000 결정 로그 | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/docs/dec/000-decision-log.md |
| AI-1 모델 API 명세 | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/docs/ai/1-model-api/spec.md |
| AI-3 아키텍처 모듈화 | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/docs/ai/3-architecture-modularization/design.md |
| AI-4 멀티스텝 파이프라인 | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/docs/ai/4-multistep-pipeline/design.md |
| AI-5 컨텍스트 보강 (폐기 콜아웃 사례) | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/docs/ai/5-context-augmentation/design.md |
| AI-6 도구 통합 (MCP 판단) | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/docs/ai/6-tool-integration/design.md |
| AI-9 데이터 ERD | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/docs/ai/9-data-erd/spec.md |
| CLD-2 CI 파이프라인 | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/docs/cld/2-ci-pipeline/overview.md |
| FS-1 테이블 명세 | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/docs/fs-1-4-conversion/docs/fs/1-table-spec/spec.md |
| FS-2 API 명세 | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/docs/fs-1-4-conversion/docs/fs/2-api/spec.md |
| FS-3 기술 스택 | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/docs/fs-1-4-conversion/docs/fs/3-tech-stack/design.md |
| FS-4 비즈니스 정책 | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/docs/fs-1-4-conversion/docs/fs/4-business-policy/spec.md |
| 문서 작성·변환 규칙 (ktb4-docs 스킬) | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/.agents/skills/ktb4-docs/SKILL.md |
| 팀 결정 상세 (결정 로그가 링크) | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/.agents/skills/ktb4-docs/references/decisions.md |

FS 문서가 main에 병합되면 `blob/docs/fs-1-4-conversion/` 부분을 `blob/main/`으로 바꾼다.

**wiki 밖 원본** (FS 문서가 가리키는 곳, 인덱싱 여부 미정)
- FS-2 API 명세의 Notion 원본: https://app.notion.com/p/3d5c174f9e7f8026be26cb360e8e1768?v=02dc174f9e7f83628d96084050756dbd&source=copy_link
- FS-1 ERD의 ERDCloud 원본: https://www.erdcloud.com/d/p5GP8brvicLfrNGQz

## 3. 문서 복사 워크플로 (중복의 원인)

| 방향 | 파일 |
|---|---|
| wiki `docs/` → AI 레포 `docs/wiki/` | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/.github/workflows/sync-docs-to-ktb4-13th-ai.yml |
| wiki `docs/` → GitHub Wiki 발행 | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/.github/workflows/wiki-sync.yml |
| wiki 문서 규칙 검사 | https://github.com/100-hours-a-week/KTB4-13th-wiki/blob/main/.github/workflows/docs-check.yml |
| AI 레포 `docs/` → wiki `docs/inbox/ai/` (PR) | https://github.com/100-hours-a-week/KTB4-13th-AI/blob/main/.github/workflows/sync-docs-to-wiki.yml |

## 4. 코드 위치

| 무엇 | 링크 |
|---|---|
| AI 라우터 (①~⑦) | https://github.com/100-hours-a-week/KTB4-13th-AI/tree/main/app/routers |
| AI 검색 구현 (가중 RRF 참고) | https://github.com/100-hours-a-week/KTB4-13th-AI/tree/main/app/search |
| AI RRF 테스트 | https://github.com/100-hours-a-week/KTB4-13th-AI/blob/main/tests/test_search_rrf.py |
| AI `main.py` ("BE 연동을 시작하면…" 주석) | https://github.com/100-hours-a-week/KTB4-13th-AI/blob/main/app/main.py |
| BE 도메인별 컨트롤러 (`<도메인>/api/*Controller.java`) | https://github.com/100-hours-a-week/KTB4-13th-BE/tree/main/src/main/java/com/book/core |
| BE 추천(③ 챗봇 연동) 패키지 | https://github.com/100-hours-a-week/KTB4-13th-BE/tree/main/src/main/java/com/book/core/recommendation |
| BE `RecommendationController` (`/api/v1/recommend`) | https://github.com/100-hours-a-week/KTB4-13th-BE/blob/main/src/main/java/com/book/core/recommendation/api/RecommendationController.java |
| BE → AI 호출 클라이언트 | https://github.com/100-hours-a-week/KTB4-13th-BE/blob/main/src/main/java/com/book/core/recommendation/infrastructure/client/ai/AiRecommendationClientImpl.java |
| BE AI 호출 설정 | https://github.com/100-hours-a-week/KTB4-13th-BE/tree/main/src/main/java/com/book/common/config/ai · https://github.com/100-hours-a-week/KTB4-13th-BE/blob/main/src/main/resources/ai.yml |
| BE 추천 카드 테이블 마이그레이션 | https://github.com/100-hours-a-week/KTB4-13th-BE/blob/main/src/main/resources/db/migration/V19__create_recommendation_cards.sql |
| BE Swagger 설정 (OpenAPI 추출 후보) | https://github.com/100-hours-a-week/KTB4-13th-BE/blob/main/src/main/java/com/book/common/config/api/SwaggerConfig.java |
| BE ADR | https://github.com/100-hours-a-week/KTB4-13th-BE/tree/main/docs/adr |
| FE 소스 | https://github.com/100-hours-a-week/KTB4-13th-FE/tree/main/src |
| CLOUD 배포 스크립트 | https://github.com/100-hours-a-week/KTB4-13th-CLOUD/tree/main/scripts |

**로컬 참고 (레포 주인 PC)**: 서비스 임베딩·검색 벤치 하니스 `~/llm/bench/` — `bench_hybrid.py`(Kiwi BM25 + e5 벡터 RRF), `eval_gold.json`(recall 골드셋 형식), `EXPERIMENTS.md`. 골든셋 형식과 recall 측정 코드를 참고할 수 있다.

## 5. 관련 이슈·PR

`/issues/N` 링크는 PR이면 GitHub가 PR로 넘겨준다.

| 번호 | 내용 | 링크 |
|---|---|---|
| AI #67 | 오케스트레이션 프레임워크 도입 결정 문서 반영 (병합됨) | https://github.com/100-hours-a-week/KTB4-13th-AI/pull/67 |
| wiki #125 | 같은 결정의 wiki 반영 (병합됨) | https://github.com/100-hours-a-week/KTB4-13th-wiki/pull/125 |
| AI #66 | LangChain 게이트웨이 전환 | https://github.com/100-hours-a-week/KTB4-13th-AI/issues/66 |
| AI #119 | ① 제목 일치 우선 + 가중 RRF | https://github.com/100-hours-a-week/KTB4-13th-AI/issues/119 |
| AI #120, #169 | ① pg_trgm만 사용, BM25·tsvector 제거 | https://github.com/100-hours-a-week/KTB4-13th-AI/issues/120 · https://github.com/100-hours-a-week/KTB4-13th-AI/issues/169 |
| AI #208 | 가격·재고를 `v_products`에서 읽기 | https://github.com/100-hours-a-week/KTB4-13th-AI/issues/208 |
| AI #219 | ①④ category 필터 | https://github.com/100-hours-a-week/KTB4-13th-AI/issues/219 |
| AI #237 | ⑥ 일부 항목 V1 미구현 | https://github.com/100-hours-a-week/KTB4-13th-AI/issues/237 |
| AI #246 | 취향 벡터 없이 이력으로 개인화 | https://github.com/100-hours-a-week/KTB4-13th-AI/issues/246 |
| BE #65 | AI 복제용 읽기 전용 계정 (OPEN) | https://github.com/100-hours-a-week/KTB4-13th-BE/issues/65 |
| BE #68 | 상품 목록 추천순·판매순 정렬 (OPEN) | https://github.com/100-hours-a-week/KTB4-13th-BE/issues/68 |

## 6. 베이스라인

| 도구 | 사용법 |
|---|---|
| Claude Code | 소스 레포를 clone한 폴더에서 골든셋 질문을 그대로 묻는다 |
| DeepWiki | `github.com`을 `deepwiki.com`으로 바꾼 주소. 예: https://deepwiki.com/100-hours-a-week/KTB4-13th-wiki (색인되지 않은 레포면 색인 요청이 필요할 수 있다) · 문서: https://docs.devin.ai/work-with-devin/deepwiki |

## 7. 구현 참고

| 주제 | 링크 | 메모 |
|---|---|---|
| MCP Python SDK | https://github.com/modelcontextprotocol/python-sdk · https://py.sdk.modelcontextprotocol.io/ | FastMCP가 SDK에 포함돼 있다 |
| Claude Code에 MCP 연결 | https://code.claude.com/docs/en/mcp-quickstart | 로컬: `claude mcp add --transport stdio <이름> -- <명령>`, 확인: `claude mcp list` |
| SQLite FTS5 (트라이그램 포함) | https://www.sqlite.org/fts5.html | 트라이그램은 3글자 미만 검색어를 매칭하지 않는다 |
| 짧은 검색어 처리 사례 | https://zenn.dev/kanseilink/articles/kanseilink-fts5-trigram-cjk-20260507?locale=en | CJK: 3글자 이상은 트라이그램, 2글자 이하는 LIKE로 따로 찾아 합침 |
| sqlite-vec | https://github.com/asg017/sqlite-vec | SQLite 벡터 검색 확장 |
| Kiwi 형태소 분석기 | https://github.com/bab2min/kiwipiepy | 서비스 벤치에서 BM25 토크나이저로 사용한 이력 |
| rank_bm25 | https://github.com/dorianbrown/rank_bm25 | 메모리 BM25 |
| multilingual-e5-small | https://huggingface.co/intfloat/multilingual-e5-small | `query: ` / `passage: ` 접두어 필수, 384차원 |
| markdown-it-py | https://github.com/executablebooks/markdown-it-py | 마크다운 파서 |
| RRF 원 논문 | https://plg.uwaterloo.ca/~gvcormac/cormacksigir09-rrf.pdf | Reciprocal Rank Fusion |
| RAG 평가 지표 (RAGAS) | https://docs.ragas.io | 충실성·답변 관련성·컨텍스트 관련성 |
| gitleaks | https://github.com/gitleaks/gitleaks | 인덱싱 전 시크릿 스캔 |
| oasdiff | https://github.com/oasdiff/oasdiff | v2 명세 ↔ 구현 규칙 비교 후보 |
| springdoc (BE OpenAPI 추출) | https://springdoc.org | v2 BE 엔드포인트 목록 추출 후보 |

## 8. 조사 자료 (plan.md §9)

| 분류 | 링크 |
|---|---|
| 팀 지식 Q&A 제품 | [Unblocked](https://docs.getunblocked.com/what-is-unblocked) · [Glean GitHub](https://www.glean.com/connectors/github) · [Copilot Spaces](https://docs.github.com/en/copilot/concepts/context/spaces) · [DeepWiki](https://docs.devin.ai/work-with-devin/deepwiki) |
| 여러 레포 코드 조사 | [Sourcegraph Deep Search](https://sourcegraph.com/docs/code-search/types/deep-search) |
| 문서 ↔ 코드 동기화 | [Swimm Auto-sync](https://swimm.io/blog/how-does-swimm-s-auto-sync-feature-work) · [Mintlify Autopilot](https://www.mintlify.com/blog/autopilot) |
| PR 리뷰 | [Greptile](https://www.greptile.com/docs/introduction) |
| API 명세 대조 | [oasdiff vs Spectral vs PactFlow](https://dev.to/deepaksatyam/openapi-contract-testing-in-2026-oasdiff-vs-spectral-vs-pactflow-and-what-i-built-21an) |
| 문서 모순 탐지 연구 | [Kensho FIND](https://kensho.com/news/can-llms-be-trusted-to-automatically-detect-inconsistencies-within-documents) · [CLAIRE](https://arxiv.org/pdf/2509.23233) · [READU](https://arxiv.org/pdf/2607.15780) |
| 벡터 RAG vs 도구 탐색 | [Claude Code Doesn't Index Your Codebase](https://vadim.blog/claude-code-no-indexing/) · [Settling the RAG Debate](https://smartscope.blog/en/ai-development/practices/rag-debate-agentic-search-code-exploration/) |
| 국내 사내 RAG | [우아한형제들 기술블로그](https://techblog.woowahan.com/25900/) (교육 운영 데이터 대상) |
