# 쿠팡 파트너스 클라이언트 및 OpenClaw 쇼핑 백엔드

표준 라이브러리만으로 구성한 최소한의 Python 기반 Coupang Partners 연동 도구 모음입니다.

이 저장소에는 현재 다음이 포함되어 있습니다.

- 재사용 가능한 Coupang Partners API 클라이언트
- 가볍게 배포 가능한 hosted 쇼핑 백엔드
- 추천 피드백용 sqlite 기반 분석 루프
- `openclaw_skill/` 아래의 공개 OpenClaw 스킬 패키지
- `bin/openclaw_shopping.py` 아래의 얇은 CLI 브리지

## 언어

- English README: [README.md](README.md)
- OpenClaw 설치/사용 가이드: [OPENCLAW-INSTALL.ko.md](OPENCLAW-INSTALL.ko.md)

## 현재 포함된 기능

- HMAC-SHA256 인증 헤더 생성
- 공통 JSON 요청 헬퍼
- `POST /deeplink`
- `POST /openapi/v2/products/reco`
- 문서화된 `products/*`, `reports/*` 엔드포인트용 얇은 래퍼
- `backend.py` 기반 hosted 쇼핑 백엔드
- 저장소 루트의 추천 엔진 및 분석 모듈
- `openclaw_skill/` 아래의 공개 백엔드 계약 문서
- 백엔드, 추천, 분석, CLI, 클라이언트 플로우 테스트

## 공개 사용 경로

일반 사용자나 OpenClaw 클라이언트는 `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY`를 설정할 필요가 없습니다. 공개 사용 경로는 hosted 서비스 `https://a.retn.kr` 입니다.

OpenClaw 설치와 실제 사용자 안내 문구는 [OPENCLAW-INSTALL.ko.md](OPENCLAW-INSTALL.ko.md)를 참고하세요.

## 빠른 시작

```python
from coupang_partners import CoupangPartnersClient

client = CoupangPartnersClient.from_env()

deeplink_response = client.deeplink(
    [
        "https://www.coupang.com/np/search?component=&q=good&channel=user",
        "https://www.coupang.com/np/coupangglobal",
    ]
)

reco_payload = client.minimal_reco_v2_payload(
    site_id="my-site",
    site_domain="example.com",
    device_id="device-id-or-ad-id",
    image_size="200x200",
    user_puid="user-123",
)
reco_response = client.get_reco_v2(reco_payload)
```

## Hosted 백엔드

공개 스킬 패키지 안에 Coupang credential을 넣지 않고, hosted 서비스가 경제적 캡처와 추천 로직을 담당하도록 설계되어 있습니다.

핵심 원칙:

- 공개 스킬과 얇은 클라이언트는 기본적으로 `https://a.retn.kr`를 사용합니다.
- 로컬 백엔드 실행은 운영자 개발용입니다.
- 제휴 딥링크 생성은 서버 쪽에서 유지해야 attribution이 운영자 쪽에 남습니다.

### 운영자 전용 로컬 백엔드 실행

```bash
export COUPANG_ACCESS_KEY="your-access-key"
export COUPANG_SECRET_KEY="your-secret-key"
export OPENCLAW_SHOPPING_API_TOKENS="replace-with-random-long-token"
export OPENCLAW_SHOPPING_DB_PATH=".data/openclaw-shopping.sqlite3"
python3 backend.py
```

이 경로는 운영자 전용입니다. 공개 사용자 기본 경로로 안내하면 안 됩니다.

### 공개 엔드포인트

- `GET /health`
- `POST /v1/public/assist`

### 보호된 엔드포인트

다음 경로는 운영자/내부 경로이며 bearer token이 필요합니다.

- `POST /internal/v1/assist`
- `POST /internal/v1/events`
- `POST /internal/v1/deeplinks`
- `GET /v1/admin/summary`

선택적으로 `X-OpenClaw-Client-Id`를 함께 보낼 수 있습니다.

### 클라이언트 호환성

- 공개 클라이언트는 기본적으로 token 없이 `https://a.retn.kr`의 public path를 사용합니다.
- 운영자 전용 내부 호출에서는 `OPENCLAW_SHOPPING_API_TOKEN` 또는 `OPENCLAW_SHOPPING_API_TOKENS`를 사용할 수 있습니다.
- `OPENCLAW_SHOPPING_BASE_URL`, `OPENCLAW_SHOPPING_BACKEND_URL`, `SHOPPING_COPILOT_BASE_URL`를 지원합니다.
- 기본 beta/public 경로에서는 stale한 localhost override가 있어도 `https://a.retn.kr`로 정규화됩니다. 예외는 `OPENCLAW_SHOPPING_ALLOW_NON_PROD_BACKEND=true`를 명시한 경우뿐입니다.
- `/internal/v1/*`를 직접 호출해야 하는 운영자/스테이징 플로우에서는 `OPENCLAW_SHOPPING_USE_INTERNAL_API=true`를 설정합니다.

### 기본 보호 장치

- `query`, `evidence_snippets` payload 길이 제한
- 프로세스 내 rate limit
- 선택적 client allowlist
- public/auth/admin 분리 rate-limit bucket
- deeplink host allowlist (`coupang.com`, `link.coupang.com`, `www.coupang.com`)

### 로컬 개발용 short-link provider

```bash
export OPENCLAW_SHOPPING_SHORTENER="builtin"
export OPENCLAW_SHOPPING_PUBLIC_BASE_URL="https://go.example.com"
```

비로컬 host로 internal/operator flow를 테스트할 때는 아래를 함께 설정합니다.

```bash
export OPENCLAW_SHOPPING_API_TOKEN="replace-with-random-long-token"
export OPENCLAW_SHOPPING_USE_INTERNAL_API="true"
```

### Cloud Run용 Firestore short-link / analytics

```bash
export OPENCLAW_SHOPPING_SHORTENER="firestore"
export OPENCLAW_SHOPPING_ANALYTICS_PROVIDER="firestore"
export GOOGLE_CLOUD_PROJECT="retn-kr-website"
export OPENCLAW_SHOPPING_PUBLIC_BASE_URL="https://a.retn.kr"
export OPENCLAW_SHORT_LINKS_COLLECTION="short_links"
export OPENCLAW_ANALYTICS_COLLECTION_PREFIX="shopping"
```

메모:

- Firestore 모드는 Cloud Run 인스턴스 교체 시에도 short-link slug를 유지합니다.
- Firestore analytics 모드는 query/recommendation/event 요약을 인스턴스 간 공유합니다.
- `FIRESTORE_EMULATOR_HOST`가 있으면 OAuth 없이 에뮬레이터를 사용합니다.
- Firestore short-link 생성이 실패해도 원본 affiliate URL로 폴백합니다.

## 공개 샘플 요청

```bash
curl -sS -X POST https://a.retn.kr/v1/public/assist \
  -H 'Content-Type: application/json' \
  -d '{
    "query": "30만원 이하 무선청소기, 원룸용",
    "constraints": {
      "must_have": ["저소음", "원룸"],
      "avoid": ["대형"]
    },
    "evidence_snippets": [
      {"text": "리뷰: 원룸에서 쓰기 좋음", "source": "manual"},
      {"text": "리뷰: 비교적 조용한 편", "source": "manual"}
    ]
  }'
```

## CLI 예시

```bash
export OPENCLAW_SHOPPING_BASE_URL="https://a.retn.kr"

python3 bin/openclaw_shopping.py \
  "30만원 이하 무선청소기, 원룸용" \
  --must-have 저소음 \
  --must-have 원룸 \
  --avoid 대형 \
  --evidence-snippet "리뷰: 자취방에서 쓰기 좋은 보조 청소기"
```

운영자 전용 internal 예시:

```bash
export OPENCLAW_SHOPPING_BASE_URL="https://a.retn.kr"
export OPENCLAW_SHOPPING_API_TOKEN="replace-with-random-long-token"
export OPENCLAW_SHOPPING_USE_INTERNAL_API="true"

python3 bin/openclaw_shopping.py "30만원 이하 무선청소기, 원룸용"
```

## OpenClaw 통합 기대치

공개 OpenClaw 스킬은 다음을 만족해야 합니다.

1. 쇼핑 요청과 예산/카테고리 힌트를 받는다.
2. 운영자가 소유한 hosted backend로 요청을 보낸다.
3. 근거 있는 추천, 이유, caveat, deeplink를 반환한다.
4. Coupang affiliate secret을 클라이언트에 요청하거나 노출하지 않는다.

자세한 public contract는 [openclaw_skill/README.md](openclaw_skill/README.md)를 참고하세요.

## 컴플라이언스 / 제품 가드레일

- 실제 key는 저장소에 저장하지 않습니다.
- 서명은 `signed-date + HTTP_METHOD + path + raw_querystring` 기준으로 생성합니다.
- 신규 작업은 `Reco v2`를 우선 사용하고, `Reco v1`은 호환용 얇은 래퍼로만 유지합니다.
- `Reco v2`의 `impressionUrl`은 실제 사용자에게 추천이 보일 때만 호출해야 합니다.
- 추천 링크를 사용자에게 보여주는 모든 위치에서는 affiliate disclosure가 필요합니다.
- evidence ingestion은 명시적이고 추적 가능해야 하며, 약한 evidence에서는 보수적으로 동작해야 합니다.
- metadata 수준의 evidence만 있을 때는 마치 상세 리뷰를 읽은 것처럼 과장하면 안 됩니다.
- 낮은 confidence의 추천은 metadata-only 한계를 명확히 드러내야 합니다.
- 실제 Coupang key나 bearer token은 절대 커밋하지 말고, runtime env 또는 Secret Manager만 사용합니다.

## 테스트

```bash
python3 -m unittest -q
```
