# PRD: Coupang Search Skill for k-skill Pack

**목표:** k-skill pack 설치 사용자가 쿠팡 API 키 없이, 설치 즉시 쿠팡 쇼핑의 모든 핵심 기능을 Claude Code에서 쓸 수 있게 한다.

---

## 1. 사용자 기대 (한국 쿠팡 유저 관점)

| 사용자 요구 | 현재 | 목표 |
|---|---|---|
| "이거 쿠팡에서 찾아줘" — 자연어 검색 | ✅ shopping-copilot | ✅ 유지 |
| "로켓배송만 보여줘" | ⚠️ 파라미터 없음, 운에 맡김 | ✅ `rocket_only` 파라미터 |
| "10만원 이하로" | ✅ 텍스트에서 파싱 (약함) | ✅ 명시적 `max_price` 파라미터 |
| "오늘 골드박스 뭐야?" | ❌ 없음 | ✅ `/v1/public/goldbox` |
| "생활용품 베스트 보여줘" | ❌ 없음 | ✅ `/v1/public/best/{category_id}` |
| "A vs B 비교해줘" | ⚠️ 자연어에 의존 | ✅ compare 플로우 |
| "평점 높은 순으로" / "리뷰 많은 순" | ✅ extremum 감지 있음 | ✅ sort 파라미터로 명확화 |
| 클릭 한 번에 구매 | ✅ deeplink 있음 | ✅ short_deeplink 우선 |
| 운영자 설정 없이 바로 동작 | ❌ coupang-product-search 크래시 | ✅ 모든 public 엔드포인트 key-free |

---

## 2. 현황 갭 분석

### 이미 있는 것 (재활용)
- `client.py`: `search_products()`, `get_goldbox()`, `get_bestcategories()`, `deeplink()` 구현 완료
- `recommendation.py`: 예산 파싱, extremum intent 감지, 정렬 로직
- `backend.py`: `/v1/public/assist` 공개 엔드포인트, rate limit, short link

### 없는 것 (추가 필요)
- `backend.py`: public 엔드포인트 3개 미노출
- `openclaw_skill/SKILL.md`: goldbox/best/rocket 트리거 패턴 없음
- `openclaw_skill/scripts/openclaw-shopping-skill.py`: 신규 엔드포인트 호출 로직 없음

---

## 3. 백엔드 명세 (backend.py 추가)

### 3-A. `GET /v1/public/goldbox`
```
응답:
{
  "ok": true,
  "data": {
    "deals": [
      {
        "title": "...",
        "price": 29900,
        "original_price": 49900,
        "discount_rate": 40,
        "deeplink": "https://link.coupang.com/...",
        "short_deeplink": "https://a.retn.kr/s/abc123",
        "is_rocket": true
      }
    ],
    "fetched_at": "2026-04-17T10:00:00Z"
  },
  "disclosure": "파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있음"
}
```
- rate limit: public_rate_limiter 적용
- 쿠팡 API `get_goldbox()` → deeplink 변환 → short link 붙여서 반환

### 3-B. `POST /v1/public/search`
```json
// 요청
{
  "keyword": "무선청소기",
  "rocket_only": true,
  "max_price": 300000,
  "sort": "SALE",          // SIM(관련성) | SALE(인기) | LOW(낮은가격) | HIGH(높은가격)
  "limit": 5
}

// 응답
{
  "ok": true,
  "data": {
    "keyword": "무선청소기",
    "results": [
      {
        "title": "...",
        "price": 211000,
        "is_rocket": true,
        "rating": 4.5,
        "review_count": 1832,
        "deeplink": "https://link.coupang.com/...",
        "short_deeplink": "https://a.retn.kr/s/xyz456"
      }
    ],
    "total": 5
  },
  "disclosure": "파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있음"
}
```
- `rocket_only=true`이면 API 결과에서 `isRocket=true` 항목만 필터
- `max_price` 있으면 가격 초과 항목 제거 (기존 `_filter_products` 재활용)
- deeplink 일괄 변환 후 short link 붙여서 반환

### 3-C. `GET /v1/public/best/{category_id}`
```
응답:
{
  "ok": true,
  "data": {
    "category_id": "1001",
    "products": [ ...same shape as search results... ]
  },
  "disclosure": "..."
}
```
- 주요 category_id 매핑 (skill에서 텍스트 → ID 변환):

| 카테고리 | category_id |
|---|---|
| 전자제품 | 1001 |
| 생활/주방 | 1010 |
| 패션 | 1015 |
| 식품/음료 | 1005 |
| 스포츠/레저 | 1012 |
| 뷰티 | 1018 |

---

## 4. 스킬 명세

### 4-A. `openclaw_skill/SKILL.md` 확장

description 트리거 추가:
- `골드박스`, `오늘 특가`, `지금 특가`, `타임딜` → goldbox 플로우
- `베스트`, `인기상품`, `카테고리 베스트` → best 플로우
- `로켓배송만`, `로켓만` → `rocket_only=true`

### 4-B. `openclaw-shopping-skill.py` 신규 커맨드

```bash
# 골드박스
python3 {baseDir}/scripts/openclaw-shopping-skill.py goldbox \
  --backend https://a.retn.kr

# 구조화 검색
python3 {baseDir}/scripts/openclaw-shopping-skill.py search \
  --backend https://a.retn.kr \
  --keyword "무선청소기" \
  --rocket-only \
  --max-price 300000 \
  --sort SALE \
  --limit 5

# 카테고리 베스트
python3 {baseDir}/scripts/openclaw-shopping-skill.py best \
  --backend https://a.retn.kr \
  --category "전자제품"
```

### 4-C. `coupang_product_search_skill` 처리
- SKILL.md를 deprecated 상태로 유지 (이미 "Operator-only"로 변경됨)
- shopping-copilot이 모든 public 트래픽을 처리

---

## 5. Agent 플로우 (라우팅 로직)

```
사용자 입력
  │
  ├─ "골드박스"/"오늘 특가" → GET /v1/public/goldbox
  │
  ├─ "베스트"/"카테고리 베스트" → GET /v1/public/best/{id}
  │
  ├─ "로켓만"/"로켓배송만" + 키워드 → POST /v1/public/search (rocket_only=true)
  │
  ├─ 키워드 + 예산/정렬 조건 명시 → POST /v1/public/search
  │
  └─ 자연어 추천 요청 → POST /v1/public/assist (기존 유지)
```

---

## 6. 성공 기준

| 기준 | 측정 방법 |
|---|---|
| k-skill 설치 후 설정 zero | COUPANG_* 환경변수 없이 5개 시나리오 통과 |
| 골드박스 응답 < 3초 | smoke test |
| 로켓 필터링 정확도 | `rocket_only=true` 결과에 `is_rocket=false` 없음 |
| deeplink 100% 포함 | 모든 결과에 `short_deeplink` 필드 존재 |
| k-skill 문서 호환 | k-skill `coupang-product-search.md`의 8개 도구 모두 커버 |

---

## 7. 구현 순서 (우선순위)

1. **백엔드 P0** — `GET /v1/public/goldbox`, `POST /v1/public/search` (rocket_only, max_price, sort)
2. **스킬 P0** — `openclaw-shopping-skill.py`에 `search`, `goldbox` 커맨드 추가
3. **SKILL.md P0** — 골드박스/로켓/정렬 트리거 추가
4. **백엔드 P1** — `GET /v1/public/best/{category_id}`
5. **스킬 P1** — `best` 커맨드 + 카테고리 텍스트→ID 매핑
6. **테스트 P0** — smoke test 5개 시나리오

---

## 8. 범위 밖 (Out of Scope)

- 로그인/장바구니/결제 자동화
- 가격 변동 알림
- 상품 이미지 표시
- 쿠팡플레이 번들
- 재고 실시간 확인 (API 미지원)
