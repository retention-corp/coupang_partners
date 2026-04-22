# Acquirer Contact Map — Acquihire Sprint 1mo

> Spec 참조: [`specs/acquihire-sprint-1mo/spec.md`](./spec.md) — **R5.1** (인수 후보 연락처 맵 운영), **R5.4** (아웃리치 로그와의 주간 동기화).
> D-Day: **2026-05-18** (목표 인바운드 도달 기한).
> 작성자: outreach-bd lead (단독 창업자 본인).
> 성격: 워킹 도큐먼트. 연락처/이름/링크드인은 비어 있는 셀에 점진적으로 채워 넣는다. 장식보다 실용.

---

## 0. 사용 규칙

- 실제 이름/이메일/링크드인은 커밋 전에 한 번 더 점검 (개인정보·NDA 리스크).
- 워밍 인트로 경로가 있으면 Cold 대신 항상 Warm 우선.
- 같은 조직 내 복수 접점은 서로 알지 못하도록 분리 관리 (중복 아웃리치 금지).
- 모든 상태 변화는 `outreach-log.md`에 타임스탬프로 먼저 기록한 뒤 이 파일의 표를 업데이트한다.

### Status enum (legend)

| Status | 의미 |
| --- | --- |
| `not-contacted` | 아직 아무 접촉도 없음. 초기 상태. |
| `warm-intro-requested` | 공통 지인에게 소개 요청을 보낸 상태. |
| `cold-outreached` | 본인이 직접 콜드 메일/DM을 보낸 상태. |
| `responded` | 상대가 회신은 했으나 미팅 확정 전. |
| `meeting-scheduled` | 미팅 일정 확정. |
| `meeting-held` | 미팅 완료. 후속 결정 대기. |
| `nda-signed` | NDA 체결 완료. 딜 검토 단계 진입. |
| `pass` | 상대가 명시적으로 거절/패스. |
| `inbound-received` | 상대가 먼저 연락해 옴 (D6 인바운드 트리거). |

---

## 1. 쿠팡 (Coupang) — **PRIMARY**

**Rationale**: 본 제품의 기본 제휴 파트너이자 수수료 수익원 (Coupang Partners). 한국 이커머스 에이전트 인텐트 라우팅 레이어의 1차 고객이며, 인수 시 affiliate 생태계 내재화 및 AI 추천 스택 보강이 가장 자연스럽다. **D6 기본 타겟**으로 지정 — 다른 모든 후보는 Plan B.

> Priority: **P0** — 모든 주간 작업은 이 섹션 진전도를 먼저 점검.

| Role/Title | Name | LinkedIn/Email | Warm-intro path | Last touch | Status | Next action | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Head of Corporate Development | | | | | `not-contacted` | | M&A 최종 의사결정권자 라인 |
| VP Product — AI / Recommendation | | | | | `not-contacted` | | 추천·랭킹 스택 오너, 기술 핏 평가 |
| Director BD — Affiliate / Partners Program | | | | | `not-contacted` | | Coupang Partners 프로그램 오너, 접점 자연스러움 |
| Head of Strategy — New Ventures | | | | | `not-contacted` | | 신사업/내부 인큐베이션 검토 |
| Engineering Director — Commerce Platform | | | | | `not-contacted` | | 커머스 플랫폼 엔지니어링 체인, 기술 DD 파트너 |

---

## 2. 네이버 (NAVER) — Plan B

**Rationale**: 검색·쇼핑·Clova AI 스택을 보유, 쇼핑 검색 인텐트에 대한 구조적 이해가 깊다. 에이전트 라우팅 레이어가 네이버쇼핑/스마트스토어와 결합할 때의 시너지가 Plan B 중 가장 크다. 단, 인수보다 전략 투자/제휴로 흘러갈 가능성도 있음.

| Role/Title | Name | LinkedIn/Email | Warm-intro path | Last touch | Status | Next action | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| CSO 산하 사내벤처/인수 담당 | | | | | `not-contacted` | | M&A·사내벤처 창구 |
| 검색본부 AI 리드 | | | | | `not-contacted` | | 검색/추천 모델 오너 |
| 쇼핑 BD 총괄 | | | | | `not-contacted` | | 쇼핑 파트너/제휴 라인 |
| Clova 기획 책임자 | | | | | `not-contacted` | | LLM·에이전트 로드맵 정합성 |
| 스타트업 투자 담당 (D2SF 등) | | | | | `not-contacted` | | 투자·인수 전환 가능성 |

---

## 3. 카카오 (Kakao) — Plan B

**Rationale**: 카카오톡 채널·선물하기·커머스 CIC를 통한 대화형 커머스 접점이 많다. 에이전트가 메신저 내 쇼핑 인텐트를 라우팅하는 시나리오와 자연스럽게 맞물린다. 다만 공동체 구조상 인수 결정이 분산될 수 있어 협상 경로 설계가 중요.

| Role/Title | Name | LinkedIn/Email | Warm-intro path | Last touch | Status | Next action | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 카카오벤처스 파트너 | | | | | `not-contacted` | | 투자 → 인수 전환 레일 |
| 공동체 CIC 담당 (커머스) | | | | | `not-contacted` | | 커머스 CIC 편입 시나리오 |
| 커머스 플랫폼 전략 책임자 | | | | | `not-contacted` | | 선물하기/쇼핑 제휴 라인 |
| AI 서비스 본부 PM | | | | | `not-contacted` | | 카카오 i / AI 에이전트 라인 |
| M&A 담당 (본사 전략실) | | | | | `not-contacted` | | 최종 인수 결재 라인 |

---

## 4. 11번가 (11st) — Plan B

**Rationale**: 쿠팡 대비 검색·제휴 레버리지가 절실한 2nd-tier 이커머스. SK스퀘어 지배구조상 IPO/매각 압력이 있어 전략적 기술 자산 편입에 열려 있을 수 있다. 플랫폼 규모는 작지만 의사결정 속도는 상대적으로 빠를 가능성.

| Role/Title | Name | LinkedIn/Email | Warm-intro path | Last touch | Status | Next action | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 전략기획 임원 (CSO/CFO 산하) | | | | | `not-contacted` | | 매각/편입 의사결정 라인 |
| 광고/제휴 본부장 | | | | | `not-contacted` | | affiliate·광고 수익 모델 정합성 |
| 신사업 담당 임원 | | | | | `not-contacted` | | AI/에이전트 파일럿 발주자 |
| IT 전략 담당 | | | | | `not-contacted` | | 기술 DD 및 통합 설계 |
| 투자/M&A 담당 (SK스퀘어 연계) | | | | | `not-contacted` | | 지주사 차원 검토 경로 |

---

## 5. 배민 (우아한형제들 / Woowa Brothers) — Plan B

**Rationale**: 음식 배달이 본업이지만 B마트·쇼핑 확장 맥락에서 커머스 인텐트 레이어 수요가 생길 수 있다. AI/추천 조직이 잘 갖춰져 있어 기술 평가는 우호적이나, 인수 비용·의사결정권은 DH(Delivery Hero) 영향을 받음에 유의.

| Role/Title | Name | LinkedIn/Email | Warm-intro path | Last touch | Status | Next action | Notes |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 전략기획 본부 책임자 | | | | | `not-contacted` | | DH 라인과의 정합성 확인 필요 |
| 커머스 신사업 담당 (B마트/쇼핑) | | | | | `not-contacted` | | 비식품 커머스 확장 접점 |
| 우아한형제들 투자 담당 | | | | | `not-contacted` | | 투자 → 인수 전환 가능성 |
| AI/추천 개발 리드 | | | | | `not-contacted` | | 기술 핏 평가 창구 |
| BD 담당 (외부 제휴) | | | | | `not-contacted` | | 제휴 파일럿 후 인수 확장 시나리오 |

---

## 6. 주간 운영 리추얼

- **매주 일요일 (Sunday sweep)**
  - `outreach-log.md`의 지난 7일 엔트리를 훑어 이 문서의 `Status` / `Last touch` 컬럼을 갱신한다.
  - 새로 확보한 이름/링크드인/이메일을 이번 주에 채워 넣은 셀만 따로 diff로 확인.
  - 상태가 `meeting-held` 이상인 타겟은 별도 메모 블록에 대화 요약/다음 결정 포인트 기록.

- **매주 월요일 (Monday stall check)**
  - 마지막 접촉 후 **2주 이상 움직임이 없는 타겟**을 식별 → 팔로업 트리거.
  - `cold-outreached` 상태에서 2주 무응답: 리마인더 1회 후 `pass`로 전환할지 결정.
  - `warm-intro-requested` 상태에서 2주 무응답: 소개자에게 상태 점검 요청, 또는 동일 조직 내 대체 접점으로 전환.
  - 스톨된 타겟 목록은 금주 아웃리치 우선순위 상단에 둔다.

---

## 7. Plan B 트리거 (D6)

**조건**: 2026-05-18(D-Day)까지 쿠팡으로부터 `inbound-received` 이상 진전이 없을 경우, 아웃리치 우선순위는 자동으로 재배열된다.

**새 우선순위**: `11번가` → `네이버` → `카카오` → `배민`

- 11번가를 선두에 두는 이유: 매각/편입 압력상 반응 속도가 가장 빠를 가능성 + 본 제품의 affiliate 수익 모델과 가장 직접적으로 결합 가능.
- 네이버/카카오: 전략 투자·제휴 쪽으로 우회할 가능성도 열어두되, 인수 프레임을 유지한 채 접근.
- 배민: 본업 거리감으로 인해 최후 순위. 단 커머스 신사업 확장 시그널이 잡히면 즉시 상향.

D6 트리거 발동 시 이 섹션에 발동 일자와 사유(쿠팡 최종 상태)를 주석으로 추가하고, 쿠팡 섹션의 Priority 라벨을 `P0` → `P1-deprioritized`로 전환한다.

---

## 8. 변경 이력

- `2026-04-18` (T18): 초기 스캐폴딩 — 5개사 × 5개 역할 아키타입(총 25개 행) 생성, 상태 enum/리추얼/Plan B 트리거 정의.
