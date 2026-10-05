# API 대조표

> 2026-10-06 · KTB4-13th-wiki `b772ba5` · KTB4-13th-AI `421a77c` · KTB4-13th-BE `8faa0c1` · KTB4-13th-FE `e27e18c` · `uv run python -m tka.contracts check`
> LLM 없이 코드·명세를 정규식으로 읽었다. 의도된 차이인지는 사람이 판단한다.

## 요약

| 명세 ↔ 서버 | 명세 | 서버 코드 | 일치 | 메서드 다름 | 명세에만 | 코드에만 |
|---|---|---|---|---|---|---|
| AI | 8 | 8 | 8 | 0 | 0 | 0 |
| BE | 40 | 34 | 14 | 1 | 25 | 19 |

| 호출 → 서버 | 호출 | 서버에 있음 | 메서드 다름 | **서버에 없음** | 경로 못 읽음 |
|---|---|---|---|---|---|
| BE→AI | 4 | 4 | 0 | 0 | 0 |
| FE→BE | 26 | 26 | 0 | 0 | 0 |

## BE→AI 호출

### 서버에 없는 경로를 부른다 · 0

없음

### 메서드가 다르다 · 0

없음

### 경로를 못 읽은 호출 · 0

없음

## FE→BE 호출

### 서버에 없는 경로를 부른다 · 0

없음

### 메서드가 다르다 · 0

없음

### 경로를 못 읽은 호출 · 0

없음

## AI: 명세 ↔ 서버 코드

명세: `KTB4-13th-wiki/docs/ai/1-model-api/spec.md`

### 메서드가 다르다 · 0

없음

### 명세에만 있다 (코드에 없음) · 0

없음

### 코드에만 있다 (명세에 없음) · 0

없음

### 아무 호출 쪽도 부르지 않는다 (참고: 외부·내부 전용일 수 있다) · 4

| 코드 | 위치 |
|---|---|
| `GET /health` | KTB4-13th-AI/app/main.py:121 |
| `POST /agent/act` | KTB4-13th-AI/app/routers/agent.py:10 |
| `POST /embeddings` | KTB4-13th-AI/app/routers/embeddings.py:53 |
| `POST /preferences/extractions` | KTB4-13th-AI/app/routers/extractions.py:10 |

### 일치 · 8

| 엔드포인트 | 명세 위치 | 코드 위치 |
|---|---|---|
| `POST /search` | KTB4-13th-wiki/docs/ai/1-model-api/spec.md:30 | KTB4-13th-AI/app/routers/search.py:52 |
| `POST /embeddings` | KTB4-13th-wiki/docs/ai/1-model-api/spec.md:31 | KTB4-13th-AI/app/routers/embeddings.py:53 |
| `POST /recommendations/chat` | KTB4-13th-wiki/docs/ai/1-model-api/spec.md:32 | KTB4-13th-AI/app/routers/chat.py:1084 |
| `GET /recommendations/feed` | KTB4-13th-wiki/docs/ai/1-model-api/spec.md:33 | KTB4-13th-AI/app/routers/feed.py:24 |
| `POST /preferences/extractions` | KTB4-13th-wiki/docs/ai/1-model-api/spec.md:34 | KTB4-13th-AI/app/routers/extractions.py:10 |
| `POST /preferences/profile` | KTB4-13th-wiki/docs/ai/1-model-api/spec.md:35 | KTB4-13th-AI/app/routers/profile.py:38 |
| `POST /agent/act` | KTB4-13th-wiki/docs/ai/1-model-api/spec.md:36 | KTB4-13th-AI/app/routers/agent.py:10 |
| `GET /health` | KTB4-13th-wiki/docs/ai/1-model-api/spec.md:37 | KTB4-13th-AI/app/main.py:121 |

## BE: 명세 ↔ 서버 코드

명세: `KTB4-13th-wiki/docs/fs/2-api/spec.md`

### 메서드가 다르다 · 1

| 경로 | 명세 | 코드 | 명세 위치 | 코드 위치 |
|---|---|---|---|---|
| `/api/v1/cart/items/{cartItemId}` | PATCH | PUT | KTB4-13th-wiki/docs/fs/2-api/spec.md:235 | KTB4-13th-BE/src/main/java/com/book/core/cart/api/CartController.java:45 |

### 명세에만 있다 (코드에 없음) · 25

| 명세 | 설명 | 비슷한 코드 경로 |
|---|---|---|
| `GET /api/v1/users/me` | 내 정보 조회 |  |
| `PATCH /api/v1/users/me` | 내 정보 수정 |  |
| `DELETE /api/v1/users/me` | 회원 탈퇴 처리 |  |
| `GET /api/v1/onboarding/questions` | 질문과 선택지 조회 |  |
| `GET /api/v1/users/me/onboarding` | 내 온보딩 상태와 답변 조회 | `GET /api/v1/onboarding` |
| `PUT /api/v1/users/me/onboarding/answers/{questionId}` | 질문별 답변 저장 및 수정 |  |
| `PUT /api/v1/users/me/onboarding/books` | 마지막 단계 도서 선택 저장 및 수정 | `PUT /api/v1/onboarding/books` |
| `GET /api/v1/items/{itemId}` | 상품 상세 조회 |  |
| `GET /api/v1/addresses` | 내 배송지 목록 조회 | `GET /api/v1/user-addresses` |
| `POST /api/v1/addresses` | 배송지 등록 | `POST /api/v1/user-addresses` |
| `POST /api/v1/addresses/{addressId}/default` | 기본 배송지 설정 |  |
| `PATCH /api/v1/addresses/{addressId}` | 배송지 수정 |  |
| `DELETE /api/v1/addresses/{addressId}` | 배송지 삭제 | `DELETE /api/v1/user-addresses/{addressId}` |
| `POST /api/v1/orders` | 주문 생성 |  |
| `POST /api/v1/cart-orders` | 장바구니 기반 주문 생성 |  |
| `GET /api/v1/orders/{orderKey}/checkout` | 주문의 결제 정보 조회 |  |
| `POST /api/v1/payments` | 결제 생성 |  |
| `POST /api/v1/payments/{paymentKey}/cancel` | 결제 전액 취소 |  |
| `POST /api/v1/payments/{paymentKey}/partial-cancel` | 결제 부분 취소 |  |
| `POST /api/v1/coupons/{couponId}/issue` | 쿠폰 발급 |  |
| `GET /api/v1/coupons/me` | 내 보유 쿠폰 조회 |  |
| `GET /api/v1/points` | 내 포인트 잔액 조회 |  |
| `GET /api/v1/reviews` | 상품 리뷰 목록 조회 |  |
| `GET /api/v1/notifications` | 내 알림 목록 |  |
| `PATCH /api/v1/notifications/{notificationId}` | 알림 읽음 처리 |  |

### 코드에만 있다 (명세에 없음) · 19

| 코드 | 위치 |
|---|---|
| `POST /api/v1/user-addresses` | KTB4-13th-BE/src/main/java/com/book/core/address/api/AddressController.java:38 |
| `GET /api/v1/user-addresses` | KTB4-13th-BE/src/main/java/com/book/core/address/api/AddressController.java:47 |
| `PUT /api/v1/user-addresses/{addressId}` | KTB4-13th-BE/src/main/java/com/book/core/address/api/AddressController.java:55 |
| `PUT /api/v1/user-addresses/{addressId}/default` | KTB4-13th-BE/src/main/java/com/book/core/address/api/AddressController.java:65 |
| `DELETE /api/v1/user-addresses/{addressId}` | KTB4-13th-BE/src/main/java/com/book/core/address/api/AddressController.java:75 |
| `GET /api/v1/onboarding/questions/{questionId}` | KTB4-13th-BE/src/main/java/com/book/core/onboarding/api/OnboardingController.java:39 |
| `GET /api/v1/onboarding` | KTB4-13th-BE/src/main/java/com/book/core/onboarding/api/OnboardingController.java:48 |
| `PUT /api/v1/onboarding/questions/{questionId}/answers` | KTB4-13th-BE/src/main/java/com/book/core/onboarding/api/OnboardingController.java:56 |
| `GET /api/v1/onboarding/books` | KTB4-13th-BE/src/main/java/com/book/core/onboarding/api/OnboardingController.java:65 |
| `PUT /api/v1/onboarding/books` | KTB4-13th-BE/src/main/java/com/book/core/onboarding/api/OnboardingController.java:74 |
| `POST /api/v1/onboarding/personalization-agreement` | KTB4-13th-BE/src/main/java/com/book/core/onboarding/api/OnboardingController.java:83 |
| `POST /api/v1/orders/checkout` | KTB4-13th-BE/src/main/java/com/book/core/order/api/OrderController.java:64 |
| `DELETE /api/v1/orders/{orderKey}/cancel` | KTB4-13th-BE/src/main/java/com/book/core/order/api/OrderController.java:74 |
| `GET /api/v1/products/{productId}` | KTB4-13th-BE/src/main/java/com/book/core/product/api/ProductController.java:32 |
| `POST /api/v1/recommend/chat` | KTB4-13th-BE/src/main/java/com/book/core/recommendation/api/RecommendationController.java:40 |
| `GET /api/v1/recommend/cards/{recommendationCardId}` | KTB4-13th-BE/src/main/java/com/book/core/recommendation/api/RecommendationController.java:50 |
| `GET /api/v1/recommend/feed` | KTB4-13th-BE/src/main/java/com/book/core/recommendation/api/RecommendationController.java:60 |
| `GET /api/v1/search` | KTB4-13th-BE/src/main/java/com/book/core/search/api/SearchController.java:32 |
| `GET /api/v1/health` | KTB4-13th-BE/src/main/java/com/book/core/support/api/SupportController.java:14 |

### 아무 호출 쪽도 부르지 않는다 (참고: 외부·내부 전용일 수 있다) · 8

| 코드 | 위치 |
|---|---|
| `GET /api/v1/orders` | KTB4-13th-BE/src/main/java/com/book/core/order/api/OrderController.java:42 |
| `GET /api/v1/orders/{orderKey}` | KTB4-13th-BE/src/main/java/com/book/core/order/api/OrderController.java:55 |
| `DELETE /api/v1/orders/{orderKey}/cancel` | KTB4-13th-BE/src/main/java/com/book/core/order/api/OrderController.java:74 |
| `GET /api/v1/recommend/cards/{recommendationCardId}` | KTB4-13th-BE/src/main/java/com/book/core/recommendation/api/RecommendationController.java:50 |
| `POST /api/v1/reviews` | KTB4-13th-BE/src/main/java/com/book/core/review/api/ReviewController.java:32 |
| `PATCH /api/v1/reviews/{reviewId}` | KTB4-13th-BE/src/main/java/com/book/core/review/api/ReviewController.java:40 |
| `DELETE /api/v1/reviews/{reviewId}` | KTB4-13th-BE/src/main/java/com/book/core/review/api/ReviewController.java:48 |
| `GET /api/v1/health` | KTB4-13th-BE/src/main/java/com/book/core/support/api/SupportController.java:14 |

### 일치 · 14

| 엔드포인트 | 명세 위치 | 코드 위치 |
|---|---|---|
| `POST /api/v1/auth/{providerType}/login` | KTB4-13th-wiki/docs/fs/2-api/spec.md:28 | KTB4-13th-BE/src/main/java/com/book/core/auth/api/AuthController.java:38 |
| `POST /api/v1/auth/reissue` | KTB4-13th-wiki/docs/fs/2-api/spec.md:35 | KTB4-13th-BE/src/main/java/com/book/core/auth/api/AuthController.java:50 |
| `POST /api/v1/auth/logout` | KTB4-13th-wiki/docs/fs/2-api/spec.md:42 | KTB4-13th-BE/src/main/java/com/book/core/auth/api/AuthController.java:61 |
| `GET /api/v1/items` | KTB4-13th-wiki/docs/fs/2-api/spec.md:161 | KTB4-13th-BE/src/main/java/com/book/core/product/api/ProductController.java:41 |
| `GET /api/v1/categories` | KTB4-13th-wiki/docs/fs/2-api/spec.md:175 | KTB4-13th-BE/src/main/java/com/book/core/category/api/CategoryController.java:20 |
| `GET /api/v1/cart` | KTB4-13th-wiki/docs/fs/2-api/spec.md:221 | KTB4-13th-BE/src/main/java/com/book/core/cart/api/CartController.java:72 |
| `POST /api/v1/cart/items` | KTB4-13th-wiki/docs/fs/2-api/spec.md:228 | KTB4-13th-BE/src/main/java/com/book/core/cart/api/CartController.java:37 |
| `DELETE /api/v1/cart/items/{cartItemId}` | KTB4-13th-wiki/docs/fs/2-api/spec.md:242 | KTB4-13th-BE/src/main/java/com/book/core/cart/api/CartController.java:54 |
| `DELETE /api/v1/cart/items` | KTB4-13th-wiki/docs/fs/2-api/spec.md:249 | KTB4-13th-BE/src/main/java/com/book/core/cart/api/CartController.java:63 |
| `GET /api/v1/orders` | KTB4-13th-wiki/docs/fs/2-api/spec.md:279 | KTB4-13th-BE/src/main/java/com/book/core/order/api/OrderController.java:42 |
| `GET /api/v1/orders/{orderKey}` | KTB4-13th-wiki/docs/fs/2-api/spec.md:286 | KTB4-13th-BE/src/main/java/com/book/core/order/api/OrderController.java:55 |
| `POST /api/v1/reviews` | KTB4-13th-wiki/docs/fs/2-api/spec.md:350 | KTB4-13th-BE/src/main/java/com/book/core/review/api/ReviewController.java:32 |
| `PATCH /api/v1/reviews/{reviewId}` | KTB4-13th-wiki/docs/fs/2-api/spec.md:357 | KTB4-13th-BE/src/main/java/com/book/core/review/api/ReviewController.java:40 |
| `DELETE /api/v1/reviews/{reviewId}` | KTB4-13th-wiki/docs/fs/2-api/spec.md:364 | KTB4-13th-BE/src/main/java/com/book/core/review/api/ReviewController.java:48 |
