---
title: "72 assist, 6 redirect, 1 click — attribution 스키마를 다시 설계한 이유"
custom_excerpt: "K-skill 편입 후 첫 42시간 실측에서 발견한 6→1 gap. Cloud Run 링크로컬 IP 문제부터 agent prefetch 패턴까지, attribution이 '작동하는 것'과 '의미 있는 수치를 만드는 것'이 어떻게 다른지 기록한다."
tags:
  - 기술 post-mortem
  - attribution
  - build-in-public
  - Coupang Partners
status: draft
published_at: "2026-04-22T21:00:00+09:00"
feature_image: null
---

## TLDR

- 2026-04-22 21:00 KST, Cloud Run 로그에서 42시간 분량 첫 실측을 뽑았다: assist 72건, shortlink redirect 7건, 쿠팡 대시보드 클릭 1건.
- shortlink redirect 6건(4/21 KST 기준)의 client_ip가 전부 `169.254.169.126`으로 찍혀 있었다. Cloud Run에서 TCP peer는 Google Front End 링크로컬 주소이고, 실제 클라이언트 IP는 `X-Forwarded-For` 헤더에 있다는 사실을 로그를 본 뒤에야 확인했다.
- 동일 슬러그 8초 내 2회 클릭, 0.4초 내 3-slug 연속 클릭 패턴은 쿠팡 서버사이드 dedup과 agent prefetch로 각각 설명됐다.
- T5에서 `X-Forwarded-For` 첫 IP → `client_ip`, User-Agent → `user_agent`, TCP peer → `proxy_ip`로 분리하고, `(client_ip, slug)` 10초 dedup 알고리즘과 일일 reconciliation을 추가했다. 223 테스트 통과.
- attribution은 스키마가 테스트를 통과하는 것과, 그 스키마가 외부 진실(쿠팡 대시보드)과 매일 비교 가능한 수치를 만드는 것 사이에 큰 거리가 있다.

---

## 1. 오프닝: 기분 좋은 숫자 하나와 불편한 숫자 두 개

2026년 4월 22일 저녁, 처음으로 실제 트래픽 데이터를 꺼내 테이블 위에 펼쳤다.

| 측정 지점 | 건수 | 집계 기간 |
|---|---|---|
| /v1/public/assist 호출 | 72건 | 4/21 06:xx ~ 4/22 21:xx KST (약 42h) |
| shortlink redirect | 7건 | 동일 |
| 쿠팡 파트너스 대시보드 클릭 | 1건 | 4/21 KST 1일치 |

72는 좋은 숫자다. K-skill(NomaDamas) 편입 직후, 별도 홍보 없이 쌓인 organic 호출이다. 그런데 72가 7로 줄고, 7이 다시 1로 줄었다. 단계마다 깎이는 숫자는 무언가를 말하고 있었다.

이 글은 그 숫자들이 무슨 이야기를 하는지 추적하고, 그 과정에서 내가 짠 attribution 스키마의 한계를 확인한 기록이다.

---

## 2. 왜 attribution이 이 시점에 중요했나

### 의도 분배권이라는 개념

[twocents.xyz의 "의도 분배권(intent distribution rights)" 글](https://twocents.xyz)은 AI 어시스턴트 레이어가 커머스 퍼널에서 차지하는 자리를 이렇게 요약한다: "어떤 플랫폼이 사용자의 구매 의도를 처음 포착하느냐가, 그 퍼널에서 가장 많은 가치를 포획하는 위치를 결정한다."

쿠팡 파트너스 연동 백엔드(a.retn.kr)는 사용자가 "무선 청소기 추천해줘"라고 입력하는 순간부터 실제 쿠팡 상품 페이지에 도달하기까지의 전 구간을 중계한다. 어시스턴트 레이어가 의도를 포착하고, 백엔드가 상품을 필터링하고, short link가 attributable한 형태로 클릭을 쿠팡에 전달한다. 그 체인이 얼마나 투명하게 측정되느냐가 "단순 파트너스 링크 중개"와 "구조적 moat의 증거자료"를 가른다.

### 1개월 sprint의 맥락

4월 18일, 1개월 sprint spec을 확정했다. 북극성 지표는 주간 활성 세션(Weekly Active Sessions)이다. 구체적 목표 수치보다 중요한 것은 측정 구조다. 수치를 만들기 전에 측정 구조가 신뢰 가능해야 한다.

K-skill 편입은 그 측정 구조를 처음으로 외부 트래픽이 통과하는 이벤트였다.

---

## 3. 스키마 v1: 5개 컬럼을 추가한 날

### T1에서 한 것

T1 작업에서 `analytics.py`의 `events` 테이블에 5개 컬럼을 추가했다.

```python
# analytics.py 라인 82-84
for column in ("surface", "surface_raw", "client_id", "client_version", "utm_source"):
    if column not in event_cols:
        connection.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT")
```

`ALTER TABLE`로 추가한 이유는 기존 sqlite DB를 날리지 않기 위해서다. Cloud Run에 이미 운영 중인 DB가 있었고, 마이그레이션은 idempotent해야 했다.

`backend.py`에는 `X-OpenClaw-Surface` 헤더를 파싱하는 `_normalize_surface` 함수를 추가했다.

```python
# backend.py 라인 71-97
def _normalize_surface(raw_value: Optional[str]) -> Tuple[str, Optional[str]]:
    if not raw_value:
        return "unknown", None
    trimmed = raw_value.strip()
    lowered = trimmed.lower()
    if lowered in _SURFACE_ENUM:
        return lowered, None
    if lowered.startswith("claw-") and len(lowered) > len("claw-"):
        return lowered, None
    # ...
    return "unknown", trimmed[:128]
```

`surface_raw` 컬럼을 별도로 유지한 이유가 있다. 알 수 없는 surface 값이 들어왔을 때 `surface`는 `"unknown"`으로 정규화하지만, 원본 문자열은 잃지 않는다. 나중에 새 surface가 추가될 때 히스토리 데이터를 소급 분류할 수 있어야 하기 때문이다.

`anonymous-<sha1(ip)[:16]>` fallback도 이때 추가됐다.

```python
# backend.py 라인 150-160
def _anonymous_client_id(remote_addr: Optional[str]) -> str:
    source = (remote_addr or "unknown").encode("utf-8")
    digest = hashlib.sha1(source).hexdigest()[:16]
    return f"anonymous-{digest}"
```

client_id 헤더 없이 온 요청도 세션 버킷이 생긴다. 의도한 것은 "anonymous traffic도 surface별로 집계 가능하게"였다.

### 테스트 통과, 그리고 안도

happy path / unknown surface / missing client_id 케이스를 각각 검증하는 테스트 3건을 추가했다. 총 테스트 수 193 → 195. 이 단계에서 나는 "attribution 인프라가 됐다"고 판단했다.

그 판단이 절반만 옳았다는 걸 3일 뒤에 알았다.

---

## 4. 첫 실측: 4/22 21:00 KST

### 숫자의 구조

Cloud Run 로그에서 42시간치를 집계했다. 72건의 assist 중 `client_id`는 전부 `openclaw-skill`이었다. K-skill의 wrapper `coupang_partners_mcp.py`가 기본값으로 `openclaw-skill`을 `X-OpenClaw-Client-ID` 헤더에 실어 보내고 있었다. attribution 스키마가 의미 있는 수치를 잡았다는 첫 증거였다. 외부 채택자가 보낸 트래픽이 내 로그에 `openclaw-skill`로 분류돼 찍혔다.

문제는 다음 단계였다.

| 단계 | 건수 | 비고 |
|---|---|---|
| assist 호출 | 72 | 전부 client=openclaw-skill |
| shortlink redirect (우리 로그) | 7 | 4/21~22 42h |
| shortlink redirect (4/21 KST만) | 6 | 쿠팡과 같은 날짜 기준 |
| 쿠팡 대시보드 클릭 | 1 | 4/21 KST |
| 구매 | 0 | - |

72 → 7은 나쁘지 않다. 추천을 받고 실제로 링크를 클릭한 비율이다. 그런데 6 → 1이 문제였다. 우리 로그는 6번의 redirect를 봤는데 쿠팡은 1번만 카운트했다.

---

## 5. 디버깅: 6에서 1이 된 이유

### 첫 번째 가설: 봇 필터

처음엔 쿠팡이 봇 트래픽을 걸러낸 거라고 생각했다. 그래서 shortlink redirect 이벤트의 `client_ip` 컬럼을 조회했다.

전부 `169.254.169.126`이었다.

6건이 모두 동일한 IP였다. 이건 봇 필터 문제가 아니었다.

### 두 번째 발견: Cloud Run의 TCP peer

`169.254.0.0/16`은 링크로컬 주소다. Cloud Run에서 TCP connection의 peer는 항상 Google Front End(GFE) 프록시다. 실제 클라이언트 IP는 `X-Forwarded-For` 헤더의 첫 번째 값에 있다.

`url_shortener.py`의 redirect handler는 `self.client_address[0]`(TCP peer)를 그대로 `remote_addr`로 읽고 있었다. `X-Forwarded-For`를 읽지 않았다. 결과적으로 모든 redirect 이벤트의 `client_ip`는 GFE 링크로컬 주소로 찍혔다. 6건이 사실상 하나의 IP로 묶인 것이다.

### 세 번째 발견: User-Agent 공란

`user_agent` 컬럼도 비어 있었다. K-skill wrapper가 User-Agent를 보내긴 하지만 redirect handler가 로깅하지 않았다. bot/preview 트래픽인지 판단할 근거가 없었다.

### 네 번째 발견: 쿠팡 서버사이드 dedup

로그를 더 자세히 보니 패턴이 두 개 있었다.

| 패턴 | 상세 |
|---|---|
| 동일 슬러그 `xdZ9Rzh`, 8초 내 2회 클릭 | 쿠팡 서버사이드 dedup → 0~1회로 집계 |
| 03시, 0.4초 내 3개 다른 슬러그 연속 클릭 | agent/preview prefetch 패턴 |

첫 번째는 쿠팡이 짧은 시간 내 동일 링크 중복 클릭을 dedup 처리한다는 걸 의미한다. 두 번째는 새벽 03시 트래픽이 실제 사람의 클릭이 아닐 가능성이 높다는 걸 시사한다. 0.4초 안에 세 개의 다른 상품 링크를 연속으로 클릭하는 것은 인간 행동 패턴이 아니다.

### gap 원인 요약

| 원인 | 설명 | 실 영향 |
|---|---|---|
| Cloud Run XFF 미처리 | TCP peer = GFE 169.254.x.x, 실 IP는 X-Forwarded-For에 | 모든 redirect IP가 동일 주소로 묶임 |
| User-Agent 미로깅 | redirect handler에서 UA를 events 테이블에 저장 안 함 | bot/preview 필터 불가 |
| 쿠팡 서버사이드 dedup | 8초 내 동일 slug 중복 클릭 → 1회로 집계 | 우리 카운트와 쿠팡 카운트 불일치 |
| Agent prefetch 패턴 | 0.4초 내 3-slug 연속 클릭 = 실 클릭 아닐 가능성 | 우리 raw count 과대 집계 |

---

이 시점에서 깨달은 것: 내가 짠 스키마가 정상적으로 작동하는 것과, 그 스키마가 의미 있는 수치를 만들어내는 것은 완전히 다른 문제다. 테스트 195개가 모두 통과했고 `client_id`는 정확히 찍혔다. 그러나 click attribution의 핵심인 `client_ip`는 전부 GFE 주소였고, `user_agent`는 공란이었다.

---

## 6. 스키마 v2: T5 보강

### 세 컬럼 추가

`analytics.py`에 `client_ip`, `user_agent`, `proxy_ip` 세 컬럼을 추가했다.

```python
# analytics.py 라인 90-93
event_cols = {row[1] for row in connection.execute("PRAGMA table_info(events)").fetchall()}
for column in ("client_ip", "user_agent", "proxy_ip"):
    if column not in event_cols:
        connection.execute(f"ALTER TABLE events ADD COLUMN {column} TEXT")
```

`record_event` 시그니처에도 세 파라미터가 추가됐다.

```python
# analytics.py 라인 162-177
def record_event(
    self,
    *,
    event_type: str,
    # ... 기존 파라미터 ...
    client_ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    proxy_ip: Optional[str] = None,
) -> str:
```

`backend.py`에는 `_first_forwarded_for` 함수를 추가해 XFF 첫 IP를 파싱한다.

```python
# backend.py 라인 117-129
def _first_forwarded_for(header_value: Optional[str]) -> Optional[str]:
    """Return the first IP from an X-Forwarded-For header, or None if absent.

    XFF is a comma-separated chain of `client, proxy1, proxy2, ...`. Cloud Run's
    front-end proxy terminates TCP, so `self.client_address` is always a link-local
    169.254.x.x peer — the first XFF entry is the real caller.
    """
    if not header_value:
        return None
    first = header_value.split(",", 1)[0].strip()
    return first or None
```

redirect handler에서 `X-Forwarded-For`의 첫 IP는 `client_ip`로, User-Agent는 200자 truncate 후 `user_agent`로, TCP peer(`self.client_address[0]`)는 `proxy_ip`로 리네임해 보존했다. 원본 TCP peer도 버리지 않는다. 나중에 프록시 체인 분석이 필요할 때 쓸 수 있다.

### dedup 알고리즘

`economics.py`의 `compute_effective_clicks`는 `(client_ip, slug)` 페어를 기준으로 10초 내 중복 클릭을 collapse한다.

```python
# economics.py 라인 291-344
def compute_effective_clicks(
    rows: Sequence[Mapping[str, Any]],
    window_seconds: int = 10,
) -> Tuple[int, int]:
    """Return (raw_clicks, effective_clicks) after collapsing near-duplicates.

    A Coupang-equivalent effective click is defined as a click on a given
    (client_ip, slug) pair that is at least window_seconds away from the
    previous click in the same pair.
    """
    # ...
    for client_ip, slug, ts in normalized:
        key = (client_ip, slug)
        if last_key != key or ts is None or last_ts is None:
            effective += 1
            last_key = key
            last_ts = ts
            continue
        gap = (ts - last_ts).total_seconds()
        if gap >= window:
            effective += 1
            last_ts = ts
    return raw_count, effective
```

10초 window는 쿠팡 dedup 기준을 정확히 모르는 상태에서 선택한 초기 가정이다. 실측 데이터가 쌓이면 조정할 예정이다.

### proxy GMV 공식과 두 개의 Source

`compute_weekly_proxy_gmv`는 주간 단위로 effective_clicks에서 proxy GMV를 추산한다.

```python
# economics.py 라인 484
proxy_gmv_krw = int(round(effective_clicks * avg_basket_krw * conv_rate))
```

기본값은 `avg_basket_krw = 30,000원`, `conv_rate = 0.015`(1.5%)다. `economics.py` 라인 18-19에 모듈 상수로 정의돼 있다.

```python
# economics.py 라인 18-19
DEFAULT_CONVERSION_RATE = 0.015
DEFAULT_AVG_BASKET_KRW = 30_000
```

이 숫자는 스펙 D10의 초기 가정이다. Source B(우리 추산)라고 부른다. Source A는 쿠팡 공식 API reporting이다. 두 개를 별도로 노출하는 이유는 "어느 쪽이 맞다"고 결론 내리기 전에 두 시계열을 병렬로 쌓기 위해서다.

### 일일 reconciliation

`compute_daily_click_reconciliation`이 핵심이다.

```python
# economics.py 라인 573-654
def compute_daily_click_reconciliation(
    date_kst: date,
    *,
    db_path: str,
    analytics_store: Any = None,
) -> Dict[str, Any]:
    # ...
    gap_ratio: Optional[float]
    if coupang_reported is None or our_effective <= 0:
        gap_ratio = None
    else:
        gap_ratio = 1.0 - (float(coupang_reported) / float(our_effective))
    # ...
    if gap_ratio is not None and gap_ratio > 0.5 and analytics_store is not None:
        analytics_store.record_event(
            event_type="click_reconciliation_warning",
            metadata={
                "date_kst": date_iso,
                "our_raw_clicks": our_raw,
                "our_effective_clicks": our_effective,
                "coupang_reported_clicks": coupang_reported,
                "gap_ratio": gap_ratio,
            },
        )
    return result
```

`gap_ratio = 1 - (coupang / our_effective)`다. 4/21 데이터에 적용하면 `1 - (1/3) ≈ 0.67`이다(effective를 dedup 후 약 3으로 가정). 임계값 0.5를 넘으므로 `click_reconciliation_warning` 이벤트가 emit된다.

경보 이벤트는 이미 emit된다. 수신처는 아직 없다. 다음 주 숙제다.

### 스키마 v1 vs v2 비교

| 영역 | v1 (T1) | v2 (T5) |
|---|---|---|
| events 컬럼 | surface, surface_raw, client_id, client_version, utm_source | +client_ip, user_agent, proxy_ip |
| redirect client IP | TCP peer (GFE 링크로컬) | X-Forwarded-For 첫 IP |
| User-Agent | 미로깅 | 200자 truncate 저장 |
| dedup | 없음 | (client_ip, slug) 10초 window |
| GMV 추산 | 없음 | effective × 30,000원 × 1.5% |
| 외부 비교 | 없음 | 일일 reconciliation + gap_ratio |
| 신규 테이블 | - | proxy_gmv, click_reconciliation |
| 신규 admin 엔드포인트 | - | /v1/admin/gmv/proxy, /v1/admin/click-reconciliation |
| 테스트 수 | 195 | 223 (+28) |

---

## 7. 남은 숙제

### 1.5% 가정의 재보정

현재 `DEFAULT_CONVERSION_RATE = 0.015`는 D10 스펙의 초기 가정이다. 4/21 실측 기준으로 raw 6, effective 약 3, 쿠팡 1건이다. gap_ratio는 약 67%다. 이 숫자가 한 주짜리 노이즈인지 구조적 gap인지 판단하려면 최소 7일치 데이터가 필요하다. 2주차 실측 후 `DEFAULT_CONVERSION_RATE`와 `DEFAULT_AVG_BASKET_KRW`를 조정할 예정이다.

### alerting 연결

`click_reconciliation_warning` 이벤트는 이미 `analytics_store.record_event`로 emit된다. 그런데 이벤트 테이블에 쌓이는 것과 실제로 알림을 받는 것은 다르다. Slack webhook 또는 이메일로 연결하지 않으면 다음 번 이상 값을 다음 주 수동 집계 때 발견하게 된다. 이건 다음 주 T6에서 처리할 예정이다.

### surface별 분해 분석

현재 72건의 assist가 전부 `openclaw-skill`이다. K-skill을 통한 단일 채널이기 때문이다. GPT Store 또는 Claude Code skill marketplace에 탑재되면 `chatgpt-gpt`, `claude-code-skill` 같은 다른 surface 태그가 붙은 트래픽이 들어온다. 그때부터 surface별 click-through rate, effective click rate 비교가 의미를 갖는다. 스키마는 준비됐다.

### is_internal_ip 필터

`economics.py`의 `is_internal_ip` 함수는 `169.254.0.0/16` 링크로컬 주소를 필터링한다.

```python
# economics.py 라인 246-266
def is_internal_ip(ip: Optional[str]) -> bool:
    """True when ip is loopback or link-local (169.254.0.0/16).

    Used by the weekly proxy-GMV batch to optionally exclude Google front-end
    proxy TCP peers that leaked into client_ip before T5 part 1 landed
    (169.254.169.126 was the concrete 2026-04-21 incident).
    """
    # ...
    return bool(addr.is_link_local)
```

T5 이전 데이터(GFE IP가 `client_ip`로 찍힌 이벤트)를 소급 분석할 때 `exclude_internal_ips=True`를 켜면 해당 이벤트를 걸러낼 수 있다. `compute_weekly_proxy_gmv`의 `exclude_internal_ips` 파라미터가 그 용도다.

---

## 8. 마치며

attribution은 수치를 만들기만 하면 끝이 아니다. 내 수치와 외부 진실의 차이를 매일 기록하는 것까지가 한 세트다.

twocents의 글은 "의도 분배권을 가진 자가 가장 많은 가치를 포획한다"고 했다. 맞는 말이다. 그런데 포획 단위를 수치로 증명하는 것은 다른 문제다. 오늘은 그 거리를 6→1로 확인했다. T5 이후에는 X-Forwarded-For가 `client_ip`에 제대로 찍히고, dedup이 쿠팡 카운트와 비교 가능한 effective click을 만든다. 다음 주에 그 거리가 얼마나 좁혀지는지 확인할 것이다.

이 글이 나올 수 있었던 것은 [NomaDamas K-skill](https://github.com/NomaDamas/k-skill/blob/main/docs/features/coupang-product-search.md)에 coupang-product-search가 편입됐기 때문이다. 첫 외부 트래픽이 없었다면 이 gap을 발견하는 데 훨씬 더 오래 걸렸을 것이다.

백엔드 OpenAPI 스펙은 [a.retn.kr/docs](https://a.retn.kr/docs)에서 확인할 수 있다. attribution 헤더 4종(`X-OpenClaw-Surface`, `X-OpenClaw-Client-ID`, `X-OpenClaw-Client-Version`, `X-UTM-Source`)의 스펙이 거기 문서화돼 있다.

---

*파트너스 활동을 통해 일정액의 수수료를 제공받을 수 있음*

---

Word count: ~2,600
