# Spec: acquihire-sprint-1mo

## Meta
- **Created**: 2026-04-18
- **Type**: dev
- **Status**: approved
- **Approved by**: user
- **Approved at**: 2026-04-18

## Goal
1개월 내 "뚜렷한 성과" 4-stack(바이럴 브랜드 + 배포 수폭 + 유저 traction + M&A 시그널)을 달성해서 Retention Inc acqui-hire 트리거. Moat = 한국 agent-shopping intent routing layer 선점. 실행 자원: solo + multi-agent. 진짜 북극성: 쿠팡/네이버/카카오 중 어느 곳이라도 acqui-hire 인바운드 미팅 1건 이상.

## Non-goals
- 팀 채용 (solo + multi-agent 자원 제약 유지)
- 쿠팡 외 해외/글로벌 시장 진출 (한국 agent-shopping 단일 초점)
- VC 펀드레이징 (M&A 이외 exit 경로 금지)
- book_reco 외 신규 vertical 0→1 구축 (1개월은 확산에 집중)
- Unit economics 최적화 (raw usage + routing share 우선)
- 쇼핑 외 카테고리(여행/금융/B2B) 피봇

## Confirmed Goal
**D-Day: 2026-05-18**까지, 쿠팡/네이버/카카오 중 1곳 이상에서 M&A 혹은 acqui-hire 인바운드 미팅 1건 이상을 확보한다. 경로는 4-stack 병렬 진행:

1. **바이럴 브랜드**: "AI-augmented growth hacker" 포지션으로 X/LinkedIn/HN/retn.kr 최소 1개 이상에서 획득적 시그널 (바이럴 포스트, HN Top 30, 한국 테크 미디어 피쳐 등).
2. **배포 수폭**: Claude Code skill marketplace, ChatGPT GPTs, Cursor extensions, Perplexity, 라이너, 뤼튼, 네이버 Cue, 카카오 ASK, LG 솔라 중 **최소 6개 서피스**에 OpenClaw shopping MCP/CLI/스킬 배포 완료 + 사용 로그 증거.
3. **유저 traction**: 본인 레이어(a.retn.kr) 통과 월간 측정치 — 누적 GMV 또는 실사용 유저 수 proxy로 공개 가능한 숫자 확보.
4. **M&A 시그널**: (1)(2)(3)을 증거로 쿠팡/네이버/카카오 BD·전략팀 라인에 직·간접 아웃리치해 인바운드 관심 1건 이상 유발.

### Mirror 추론 (literal 요청 너머)
- 인터뷰 전 요청은 "제품 오퍼링 확장 → 돈 더 벌기"였으나, 진짜 북극성은 **revenue가 아닌 acqui-hire 인바운드**. 따라서 모든 subgoal은 "미팅 트리거에 기여하는가?"로 평가.
- "배포 수"와 "라우팅 점유"는 같지 않음. 6개 서피스 배포는 필요조건, 충분조건은 "각 서피스에서 한국어 쇼핑 intent의 의미 있는 비율이 본인 레이어로 오게 만드는 것". 카운팅은 installs/sessions 둘 다 필요.

## Research

### Agent surface 현황 (2/6 built)
- Claude Code skill `shopping-copilot` — trigger: 쇼핑 추천/뭐 사야 해/골라줘/로켓배송만/골드박스/베스트 (`openclaw_skill/SKILL.md:8-21`).
- Operator-only Coupang MCP `coupang-product-search` — requires local COUPANG_ACCESS_KEY/SECRET_KEY (`coupang_product_search_skill/SKILL.md:15-30`).
- Thin CLI bridge against hosted backend (`bin/openclaw_shopping.py`) — no credentials, default `https://a.retn.kr`.
- **Missing (gap to 6)**: ChatGPT GPT, Cursor extension, Perplexity, 라이너, 뤼튼, 네이버 Cue, 카카오 ASK, LG 솔라. 상용 surfaces 0개.

### book_reco vertical (70% built, not wired)
- Providers ready: Naver / Data4Library / NLK / Saseo / Fallback in `book_reco/providers/`.
- `book_reco/coupang_bridge.py:1-204` already does title+author→Coupang search with ISBN validation.
- Entrypoints exist: `book_reco/cli.py`, `book_reco/mcp_server.py`, `book_reco/backend_integration.py`.
- **Missing**: book vertical wiring into main `backend.py` assist flow (per `docs/book-reco/INTEGRATION-NOTES.md:34-48`).

### retn.kr 브랜드 인프라
- Domain live at `a.retn.kr` (Cloudflare DNS + Cloud Run `a-retn-shortener`).
- 블로그 드래프트 존재: `docs/blog/agentic-commerce-openclaw-coupang-beta.md` — 아직 미배포.
- 공개 랜딩/API docs 페이지 없음 — OpenAPI spec 미배포.
- Korean README: `OPENCLAW-INSTALL.ko.md`, `README.ko.md` — 인스톨 가이드 있음.

### Public endpoints (discoverability 낮음)
- `GET /health`, `POST /v1/public/assist|search|deeplinks|events`, `GET /v1/public/goldbox`, `GET /v1/public/best/{category}`, `GET /s/{slug}` (`backend.py:389-523`).
- Protected bearer routes: `/v1/assist|events|deeplinks|admin/summary`.
- Minimal SEO/discoverability — SKILL.md만 존재, OpenAPI 또는 공개 spec 없음.

### Analytics & measurement (대형 gap)
- SQLite analytics store: queries/recommendations/events/evidence (`analytics.py:16-166`).
- Event logging widely instrumented in `backend.py` (20+ log_event 호출).
- **Missing**: ① click→purchase GMV 전환 미추적, ② surface별 routing share attribution 없음 (ChatGPT vs Claude Code 등 origin 구분 불가), ③ Firestore migration 미배포 (`docs/REMAINING-WORK.md:42-54`).
- `economics.py` 존재 — GMV 트래킹 확장 필요.

### Sprint critical path (L2 이후 재정렬됨)
1. **백엔드를 "채택 가능한 플랫폼"으로 승격** (a.retn.kr 공개 API + SDK + MCP + skills) — 의도 분배권 획득 경로.
2. **Developer-facing surface 배포** (GPT, Codex, Claude, Claude Code, OpenClaw/claw 계열, CLI, MCP).
3. **Measurement** — WAS (주간 활성 세션) + surface origin attribution + GMV proxy.
4. **Build-in-public 브랜드** (blog + X/LinkedIn + HN).
5. **Acquirer outreach** — 쿠팡 우선, 11번가/네이버/카카오/배민 백업 라인.

## Decisions

### D1: 타겟 유저 = 한국 유저 한정, consumer AI 소규모 플랫폼 제외
- **Status**: resolved
- **Rationale**: 사용자가 명시적 방향 지정 — 뤼튼 등 "잔잔바리" 스킵, 한국 유저 밀도 높은 대형 구간에만 에너지 집중. 글로벌/영미권은 Non-goal에 이미 포함. 거부 대안: ① 전 세계 AI 유저 → 리소스 분산 ② 글로벌 영미 agent 유저 중심 → acquirer(쿠팡/네이버/카카오)와 무관.

### D2: Surface 전략 = developer-facing integrations 전용
- **Status**: resolved
- **Rationale**: 사용자 명시 — "gpt, codex, claude, claude code, claw 류, cli, mcp 정도만 만들어놓자". 선정 surface: ChatGPT GPT, OpenAI Codex, Claude (Projects/공식 skill), Claude Code skill, OpenClaw / claw 계열, 공개 CLI, 공개 MCP. **제외**: 네이버 Cue(정식 API 부재), 카카오 ASK(정식 API 부재), Perplexity, Cursor, LG 솔라, 라이너, 뤼튼. 거부 대안: ① consumer AI 플랫폼 병행 → 승인·연동·심사 리스크 대비 ROI 낮음 ② 8개 이상 surface → solo + 1개월 capacity 초과.

### D3: Moat = "a.retn.kr 백엔드를 developer들이 채택" (B2D platform)
- **Status**: resolved
- **Rationale**: 사용자 명시 — "백엔드를 만들고 다른 사람들이 내 뱅넨드를 채택하면 난 꽁짜로 돈 버는거지". twocents 글의 "의도 분배권(intent distribution rights)" thesis와 정확히 정렬 — 개별 surface install보다 **백엔드 통과율이 실질 moat**. Developer가 통합할수록 passive 통과 세션 증가 + affiliate 수수료도 passive 증가. 거부 대안: ① direct consumer product → solo capacity로 UX 경쟁 불가 ② pure SaaS subscription → 1개월 내 유료 전환 불가.
- **Steelman 반박 (L2-reviewer 발굴)**: "브랜드 스케일은 consumer/acquirer mindshare로 흘러가서 developer adoption과 역방향이다." → **반박**: D3 moat은 주로 D8의 **angle ② 기술 post-mortem** + 공개 OpenAPI/SDK로 쌓는다. D8 angle ①(build-in-public) + ③(시장론)은 acquirer-funnel fuel로 분리 목적. D8에서 이 역할 분담을 명시(아래).

### D4: Routing share 주지표 = 주간 활성 세션 (WAS)
- **Status**: resolved
- **Rationale**: 사용자 채택. WAS는 routing layer 성격과 가장 정합 — 한 세션 안에서 백엔드 통과한 모든 agent 쿼리 수. GMV는 supplement (Coupang Partners API reporting 기반). 거부 대안: ① attributed query 수 → 낮은 수준 metric, acquirer 에게 무의미 ② GMV 단일 → 초기 sprint엔 0원 ≠ 0 traction, underreport 위험 ③ 3-metric 병행 → dashboard 복잡도 증가, 명료도 희석.
- **정의 (pinning)**:
  - 1 session = `client_id` × 30분 idle-timeout (이내 모든 요청은 단일 세션으로 카운트). Timeout 후 새 요청은 새 세션.
  - `client_id` = 요청 헤더 `x-openclaw-client-id` 필수 (발급 토큰 당 UUID v4). 미제공 요청은 `anonymous-<IP hash>` 로 fallback, anonymous는 별도 집계.
  - Cross-surface stitching: **하지 않음**. 동일 `client_id`도 surface별로 분리 집계(surface-origin 헤더 기준). Developer adoption metric 목적에 맞음.
  - WAS = 최근 7일간 ≥1개 유효 쿼리를 발생시킨 고유 session 수.

### D5: book_reco vertical = park (스프린트 중 동결)
- **Status**: resolved
- **Rationale**: 사용자 채택. 70% 상태로 프리즈, integration 작업 중단. 1개월 내에 책 시장 확장은 의도 분배권 테마와 분산 효과. 스프린트 후 바로 재개할 수 있도록 현재 브랜치/파일 상태 그대로 보존. 거부 대안: ① v1 런칭 → 통합 작업으로 2주 소진, 배포 수폭·브랜드 둘 다 지연 ② demo만 노출 → 반쯤 걸친 상태로 브랜드 혼선.

### D6: Plan B = 더 작은 acqui-hire 수용
- **Status**: resolved
- **Rationale**: 사용자 채택. D-Day 쿠팡 inbound 0건 시 11번가 / 네이버 / 카카오 / 배민 라인으로 acqui-hire 타깃 확장. 스프린트 연장(동일 전략 한달 더)이나 SDK 수익화 pivot 모두 거부 — 사용자의 "1개월 시한" 원칙 유지 + exit 우선순위 고수. 거부 대안: ① 스프린트 연장 → 데드라인 의지 약화, "조금만 더" 함정 ② B2B SDK pivot → revenue 확보에 시간 걸림, M&A 동력 상실 ③ 전면 피봇 → 1개월 투자 매몰비용.

### D7: 마케팅 예산 = 월 50-100만원 중예산, 2-audience 분할
- **Status**: resolved
- **Rationale**: 사용자 채택. **Audience split (L2-reviewer 반영)**:
  - **70% (35-70만원): 한국 개발자 타깃** — X 부스팅(개발자 계정 겨냥), GitHub sponsor tier 노출, HN submission 크레딧, dev.to 승격. 목적: D3 backend adoption 직접 구동.
  - **30% (15-30만원): 한국 startup/BD 에코시스템** — LinkedIn 부스팅(스타트업 임원/BD 타깃), Rocketpunch/Demoday 같은 채널. 목적: acquirer pitch inbound 신호 증폭.
  - ROAS 추적: D9 attribution 스키마 기반. 광고 클릭→세션 conversion을 `utm_source` + `client_id` 부여로 추적.
  - 거부 대안: ① 단일 audience(개발자만) → acquirer exec reach 누락, D8의 ①③ angle 효과 감소 ② 단일 audience(exec만) → D3 moat 구동력 부족.

### D8: 브랜드 narrative = 3-angle stack, 역할 분리 명시
- **Status**: resolved
- **Rationale**: 사용자 "전부" 응답. **역할 분담 (L2-reviewer 반영)**:
  - **Angle ② 기술 post-mortem (moat-building)** — D3 B2D adoption 직접 드라이버. retn.kr 블로그 장문 3-5편 + 오픈소스 레포 README. "내가 한국 agent shopping backend를 이렇게 설계했다" 각도로 integration 욕구 자극. **주 출력 채널: retn.kr 블로그 + GitHub**.
  - **Angle ① build-in-public (acquirer-funnel fuel)** — 데일리/격일 X 포스팅으로 momentum 시각화. Sprint 진행도, 수치, 승인 히스토리 공개. 목적: acquirer가 "빠르게 움직이는 팀"으로 인지. **주 출력 채널: X**.
  - **Angle ③ 시장론 (acquirer-funnel fuel)** — twocents thesis 확장, "한국 agent commerce de facto layer"선언. HN submission + LinkedIn 장문 × 1-2회. 목적: acquirer BD/전략팀 feed에 닿기. **주 출력 채널: HN + LinkedIn**.
  - 분담 원칙: moat는 ②가 단독 구동, ①③은 M&A trigger 가속. 역방향 충돌 없음 — 각각 다른 채널·오디언스·콘텐츠 포맷.
- 거부 대안: 단일 각도 집중 → multi-audience (개발자/운영자/acquirer) 리치 누락.

### D9: Surface-origin attribution 스키마 (concrete)
- **Status**: resolved
- **Rationale**: 기존 `backend.py` event logging 인프라(`log_event`, `record_event`) 확장. 거부 대안: 별도 이벤트 스토리지 신설 → 1개월 capacity 초과.
- **Schema (pinning)**:
  - Required headers on all `/v1/public/*` endpoints:
    - `x-openclaw-surface`: `claude-code-skill` | `chatgpt-gpt` | `codex` | `claude-project` | `openclaw-skill` | `claw-{variant}` | `cli` | `mcp` | `unknown`
    - `x-openclaw-client-id`: UUID v4 (client-generated, persistent)
    - `x-openclaw-version`: integrator-provided semver (best-effort)
    - `utm_source` (query string, optional): ad attribution
  - Enum 외 값 수신 → `unknown` 으로 normalize + raw 값 로그. Warning만, 거절 X.
  - `analytics` table에 3개 컬럼 추가: `surface`, `client_id`, `utm_source`.
  - 기존 `anonymous-<IP hash>` fallback 유지 (누락 client_id 처리).

### D10: GMV 추적 범위 = Coupang Partners reporting + shortlink click proxy (concrete)
- **Status**: resolved
- **Rationale**: 완전 attribution은 1개월 내 구현 비용 큼. 거부 대안: full attribution integration → 2주+ 소요.
- **Formula (pinning)**:
  - **Source A (확정 GMV)**: Coupang Partners API 주간 reporting import → 주단위 total 확정 GMV + commission 확보. Daily cron(Cloud Scheduler) 1회. 단, surface별 attribution은 불가(Coupang API 한계).
  - **Source B (proxy GMV)**: `a.retn.kr/s/<slug>` redirect 로그에서 surface별 click → 평균 객단가 × 추정 전환율(초기 가정 1.5%, 추후 Source A로 보정) = surface별 proxy GMV.
  - **Public dashboard 노출**: Source A 수치는 실수치로, Source B는 "attributed estimate" 라벨 붙여 분리 노출. Acquirer pitch 시 둘 다 제시.

### D11: Surface 승인 실패·배포 차단 circuit-breaker (신규, L2-reviewer 반영)
- **Status**: resolved
- **Rationale**: ChatGPT GPT Store, Claude Code skill marketplace, OpenAI Codex 등 승인 절차가 D-Day 내 완료 안 될 수 있음. Known Gap에 있던 "사이드로드 대체"를 구체화.
- **Protocol**:
  - **Hard ship = 완전 배포**: 스토어 승인 + public install 가능 + 성공 사례(최소 1건 외부 사용자 사용 증거).
  - **Soft ship = 사이드로드**: 스토어 승인 없이 공개 레포 + 설치 가이드로 manual 설치 가능. 성공 로그(최소 3 client_id 유입) 확보.
  - **Announcement-only = 최후 수단**: 승인·사이드로드 모두 불가 시, public teaser/waiting list만 공개. WAS 카운트에 포함 안 함.
  - D-Day 평가 시 "6개 surface 배포 완료" 조건은 **hard ship ≥ 3 + soft ship ≥ 3** 로 redefine. Announcement-only는 배포 수로 카운트하지 않음.
  - 거부 대안: ① all-or-nothing hard ship만 인정 → 승인 타임라인 불확실성으로 실패 리스크 과다 ② 수단 무관 배포 수 인정 → announcement-only 인플레이션 위험.

## Constraints
- **자원**: solo + multi-agent orchestration. 팀 채용 금지.
- **언어/플랫폼**: Python 3.11 표준 라이브러리만 (requirements.txt empty 유지). Cloud Run `a-retn-shortener` 서비스.
- **거리**: Coupang Partners TOS 준수 — `DISCLOSURE_TEXT` 유지, affiliate 딥링크 서버사이드 생성, deeplink host 허용 리스트 `security.validate_deeplink_url`.
- **시장 스코프**: 한국 유저 단일 (non-goal: 해외).
- **마케팅 상한**: 월 1,000,000원 이하.
- **vertical 동결**: 스프린트 중 book_reco 외 신규 vertical 0→1 금지.
- **Exit 경로**: M&A 또는 acqui-hire만 (VC 펀드레이징 금지).

## Known Gaps
- **Surface 승인 일정 불확정**: D11 circuit-breaker(hard/soft/announcement ship)로 완화했으나 타임라인 자체는 여전히 외부 의존.
- **Coupang Partners API 변경 리스크**: Access 제재/rate limit 변경 발생 시 fallback 미설계. 백업: 11번가/네이버 쇼핑 affiliate API 조사 필요 (스프린트 내 리서치만 예정).
- **경쟁자 선제 런칭 탐지**: K-skill 확장 / Coupang 내부 agent 프로젝트 움직임 모니터링 메커니즘 미구축 — 수동 주간 체크로 대체.
- **Acquirer 아웃리치 시퀀스**: 쿠팡 내 BD/전략/M&A 팀 접점 미구체. 인맥 매핑 + warm intro 경로 탐색은 sprint 주1 체크포인트로.
- **L2 provisional: Surface set enum 확장 가능성**: ChatGPT Codex가 독립 surface인지, Claude Projects와 Claude Code skill 구분이 유의미한지 실행 중 재평가 필요 — D9 enum에 `unknown` fallback으로 연결.
- **전환율 1.5% 초기 가정**: D10 Source B proxy 계산의 initial conversion rate는 검증 데이터 없음. Source A 주간 reporting 받자마자 backfill 보정 필요.

## Complexity Classification
**Complex (6+ signals)** — external APIs (Coupang/Naver/Data4Library/NLK), 3+ subsystems (backend+skills+MCP+brand+outreach), multi-stage flow (build→distribute→measure→outreach), async resources, multi-actor (user/operator/acquirer/reviewer), schema design (attribution/GMV).

**Brownfield adjustment 적용** — weights: Core 30%, Scope 20%, Error 25%, Data 15%, Implementation 10%. L1 auto-resolve가 Implementation 3/3 + 기초 infra 커버.

## Requirements

### R0: Acqui-hire inbound signal by D-Day 2026-05-18 (goal-level)

#### R0.1: Inbound M&A/acqui-hire meeting from target acquirer
- **Given**: 스프린트가 D1-D11 decisions 대로 실행되고 outreach/브랜드 활동이 누적됨
- **When**: 2026-05-18 23:59 KST
- **Then**: 쿠팡·네이버·카카오·11번가·배민 중 1곳 이상 BD/전략/M&A 담당자로부터 인바운드 미팅 요청 ≥1건이 `inbound-log.md`에 기록되어 있고, 출처·역할·일자·요청 타입이 검증 가능

#### R0.2: 4-stack proxy signal pitch page
- **Given**: 메트릭 수집(R3) + 브랜드 활동(R4) + 배포(R2)가 운영 중
- **When**: Acquirer가 `https://retn.kr/acquihire-pitch` 공개 URL 접근
- **Then**: 페이지가 ① 배포 surface 리스트(hard/soft 라벨) ② 최근 7일 WAS 차트 ③ GMV(Source A 실측 + Source B estimate) ④ 브랜드 signal 인벤토리(블로그/X/HN 링크) 4개 섹션을 렌더

### R1: Korean agent-shopping intent routing layer (D1/D3/D4)

#### R1.1: Backend receives & persists surface-tagged assist requests
- **Given**: 요청 `POST /v1/public/assist` with headers `x-openclaw-surface=chatgpt-gpt`, `x-openclaw-client-id=<uuid-v4>`, body = valid assist payload
- **When**: 백엔드가 요청 처리
- **Then**: 정상 200 응답 + `analytics` 테이블에 `surface='chatgpt-gpt'`, `client_id='<uuid>'`, `event_type='assist'` row 1개 기록

#### R1.2: Public OpenAPI 3.x spec is discoverable
- **Given**: 외부 개발자 client (curl / Swagger UI)
- **When**: `GET https://a.retn.kr/openapi.json`
- **Then**: 200 + 유효한 OpenAPI 3.x document. `/v1/public/assist|search|deeplinks|events`, `/v1/public/goldbox`, `/v1/public/best/{category}`, `/s/{slug}` 모두 문서화. 헤더 스키마(`x-openclaw-surface` enum, `x-openclaw-client-id`) 포함

#### R1.3: WAS admin query endpoint
- **Given**: 최근 7일 `analytics` 테이블에 여러 client_id × surface 조합의 assist 이벤트 존재
- **When**: `GET /v1/admin/was?window=7d&surface=claude-code-skill` with bearer token
- **Then**: 200 + `{"was": <int>, "distinct_clients": <int>, "window_start": "<iso>", "window_end": "<iso>", "surface": "claude-code-skill"}`

#### R1.4: 30-min idle session timeout
- **Given**: `client_id=X` 가 `t=0s`, `t=28min`, `t=45min` 3개 요청 발생
- **When**: session aggregation 실행
- **Then**: `t=0s` + `t=28min` = session_id=S1 (1개 세션으로 묶임); `t=45min` = session_id=S2 (새 세션). WAS에 동일 client_id 당 2개 세션 기록

#### R1.5: Surface enum normalization
- **Given**: 요청 `x-openclaw-surface=some-unseen-value`
- **When**: 백엔드 이벤트 로깅
- **Then**: `surface='unknown'` 정규화되어 저장, `surface_raw='some-unseen-value'` 별도 컬럼에 보존, 경고 로그 1건 emit, 요청 자체는 거절되지 않음

### R2: Developer-facing surface distribution (D2/D11)

#### R2.1: ChatGPT GPT Store 제출
- **Given**: GPT manifest + OpenAPI action + privacy policy 준비 완료
- **When**: 스프린트 D-14 이전
- **Then**: OpenAI GPT builder에서 "under review" 또는 "published" 상태. 스크린샷 또는 public URL이 `ship-log.md`에 기록

#### R2.2: OpenAI Codex platform integration
- **Given**: Codex custom tool definition + credential flow 준비
- **When**: Sprint 중 배포 실행
- **Then**: Hard ship(공식 marketplace) 또는 soft ship(public repo + install guide). 외부 client_id ≥ 3건 이상이 실 요청 로그에 나타남

#### R2.3: Claude Project / Claude Code skill marketplace ship
- **Given**: `openclaw_skill/SKILL.md` 이미 존재 + Claude Code skill marketplace 요구사항 맞춤
- **When**: Marketplace 제출 + 공개
- **Then**: Hard ship — 마켓 리스팅 live + ≥1 외부 설치자가 `x-openclaw-surface=claude-code-skill` 헤더로 요청 전송 확인

#### R2.4: 공개 Python CLI 패키지
- **Given**: `bin/openclaw_shopping.py` 기반 패키징 완료
- **When**: `pip install openclaw-shopping` 명령
- **Then**: PyPI 에서 설치 성공 + 설치된 CLI가 a.retn.kr 에 `x-openclaw-surface=cli` 로 요청 전송. 외부 설치자 client_id ≥ 1 로그

#### R2.5: 공개 MCP 패키지 (operator creds 불필요 버전)
- **Given**: 기존 `coupang_product_search_skill/` 은 operator-only. 공개 MCP는 a.retn.kr 백엔드를 호출하는 thin wrapper로 신규 빌드
- **When**: MCP registry(또는 GitHub public repo + 설치 가이드) 배포
- **Then**: Hard ship — 외부 사용자 1명 이상이 설치 후 성공 응답 로그, `x-openclaw-surface=mcp` 태깅

#### R2.6: OpenClaw/claw 계열 integration
- **Given**: OpenClaw PR template / claw 계열 API 스펙
- **When**: Sprint 중 PR/publish
- **Then**: 최소 soft ship — 공개 레포 + 설치 가이드 + 외부 사용 client_id ≥ 3

#### R2.7: D-Day distribution gate
- **Given**: 2026-05-18 00:00 KST
- **When**: `ship-log.md` 최종 집계
- **Then**: `hard_ship_count >= 3 AND soft_ship_count >= 3`. Announcement-only 는 총 카운트 배제. 만족 안 되면 R0.2 pitch 페이지에 "distribution target missed" 섹션 표시

### R3: Measurement & attribution (D9/D10)

#### R3.1: 4-header attribution schema 확장
- **Given**: `analytics.py` 테이블 마이그레이션 필요
- **When**: 스프린트 초반 ALTER TABLE 실행
- **Then**: `analytics` 테이블에 `surface TEXT`, `client_id TEXT`, `client_version TEXT`, `utm_source TEXT` 4개 컬럼 추가. 신규 요청 모두 이 4개 값을 (fallback 포함) 저장

#### R3.2: Coupang Partners weekly reporting cron
- **Given**: Cloud Scheduler + Cloud Run endpoint 연결 + Coupang Partners API credential
- **When**: 매일 09:00 KST 트리거
- **Then**: 최근 7일 거래 리포트 fetch → `economics` 테이블 upsert (중복 key 스킵) → `GET /v1/admin/gmv` 엔드포인트에서 주간 확정 GMV + commission 반환

#### R3.3: Shortlink click-to-proxy-GMV pipeline (effective clicks 기반)
- **Given**: `a.retn.kr/s/<slug>` redirect 이벤트가 `x-openclaw-surface` + `client_id` + 실 X-Forwarded-For IP + User-Agent로 태깅된 상태 (R3.5/R3.6로 선결)
- **When**: 주간 aggregation 배치 실행 (일요일 23:00 KST)
- **Then**: `proxy_gmv` 테이블에 `(surface, week_start, raw_clicks, effective_clicks, estimated_basket_krw, proxy_gmv_krw, label='attributed_estimate')` row 기록. 공식: `proxy_gmv_krw = effective_clicks × avg_basket × 0.015` (1.5% 전환율 초기 가정). **`effective_clicks` = R3.7 dedup 적용 후 값**.

#### R3.5: Shortlink redirect에 실 client IP 캡처 (X-Forwarded-For)
- **Given**: Cloud Run 앞단에 Google 프런트엔드 프록시가 있어 TCP peer IP는 `169.254.169.126` 등 링크-로컬 주소로 뭉침. 2026-04-21 실측 6개 redirect 전부 내부 IP로 찍혀 bot 판별·dedup 불가했음.
- **When**: `GET /s/<slug>` 핸들러가 요청을 처리할 때
- **Then**: `X-Forwarded-For` 헤더의 첫 번째 IP를 `client_ip` 컬럼에 기록. 헤더 없으면 TCP peer를 fallback. 기존 `remote_addr` 는 `proxy_ip` 로 리네임 보존.

#### R3.6: Shortlink redirect에 User-Agent 로깅
- **Given**: 현재 shortlink redirect 이벤트에 User-Agent 공란. Bot/preview 필터링 불가.
- **When**: redirect 이벤트 emit
- **Then**: `User-Agent` 헤더 원문을 `user_agent` 컬럼에 기록. 200자 초과 시 truncate.

#### R3.7: 동일 슬러그 10초 내 dedup (effective click 계산)
- **Given**: Coupang 대시보드가 `(같은 IP/세션, 같은 상품, 짧은 시간)` 클릭을 dedup하여 집계. 2026-04-21 실측: 동일 슬러그 `xdZ9Rzh` 8초 내 2회 → Coupang 1회로 카운트.
- **When**: 주간/일일 aggregation이 `(client_ip, slug)` 당 클릭 목록 처리
- **Then**: 같은 `(client_ip, slug)` 쌍에서 10초 이내 연속 클릭은 1회로 병합해 `effective_clicks` 계산. `raw_clicks` 는 별도 보존.

#### R3.8: 매일 Coupang vs 자체 click 비교 리포트
- **Given**: Coupang Partners 리포트 API (R3.2의 weekly cron과 공유) + 자체 shortlink 로그
- **When**: 매일 22:00 KST cron (Coupang 전일자 반영 16:00 이후로 안전 마진 확보)
- **Then**: `click_reconciliation` 테이블에 `(date_kst, our_raw_clicks, our_effective_clicks, coupang_reported_clicks, gap_ratio)` row 생성. `gap_ratio = 1 - (coupang / our_effective)`. >50% 면 slack/이메일 경고 (2026-04-21 실측 gap 83%는 initial baseline).

#### R3.4: Public pitch dashboard 렌더러
- **Given**: R3.1~R3.3 데이터 + R2 ship-log + R4 content-log + R5 outreach/inbound-log
- **When**: 방문자가 `https://retn.kr/acquihire-pitch` 접근
- **Then**: 1개 페이지에 4 섹션 렌더: (1) Surface ship list (hard/soft 라벨), (2) WAS 7일/28일 차트, (3) GMV Source A vs Source B 테이블, (4) 최신 브랜드 signal 5개. 정적 또는 캐시(≤ 1시간) 허용

### R4: Brand narrative & paid distribution (D7/D8)

#### R4.1: Technical post-mortem 장문(angle ②, moat driver)
- **Given**: retn.kr Ghost CMS 운영 중 + `docs/blog/agentic-commerce-openclaw-coupang-beta.md` 드래프트 존재
- **When**: 스프린트 종료 시점(D-Day)
- **Then**: retn.kr에 "기술 post-mortem" 태그로 분류된 ≥ 2000 단어 글 ≥ 3편 퍼블리시. 각 글은 a.retn.kr/openapi.json 또는 GitHub 레포 inbound 링크 ≥ 1개 포함

#### R4.2: Build-in-public on X (angle ①, acqui-hire fuel)
- **Given**: X 계정 운영 + 스프린트 진행
- **When**: 스프린트 기간(4주)
- **Then**: 주 평균 ≥ 4건, 누적 ≥ 20건 포스트. 각 포스트는 최소 1개 측정 가능 숫자(WAS, 배포 surface 추가, GMV delta 등) 포함

#### R4.3: 시장론 장문(angle ③, acqui-hire fuel)
- **Given**: HN + LinkedIn 계정 운영 가능
- **When**: 스프린트 기간
- **Then**: HN Show/Ask submission ≥ 1건 + LinkedIn 장문(> 1000자) ≥ 2편. 모두 "Korean agent commerce intent routing" thesis 각도 포함 + retn.kr 블로그 크로스링크

#### R4.4: Paid spend 예산·attribution 통제
- **Given**: 월 예산 500k-1,000k KRW, 70/30 audience split (dev/exec)
- **When**: 광고 캠페인 실행
- **Then**: 총 지출 ≤ 1,000k KRW. 각 ad unit에 `utm_source=<campaign>` 부여 → D9 headers 로 session 로 conversion trace 가능. `spend-log.md`에 주간 지출·클릭·conversion 기록

### R5: Acquirer outreach & Plan B (D6)

#### R5.1: Acquirer contact map 작성
- **Given**: 스프린트 D1-D3
- **When**: 작성 완료
- **Then**: `specs/acquihire-sprint-1mo/acquirer-targets.md` 에 쿠팡·네이버·카카오·11번가·배민 각 acquirer 당 ≥ 5명 (이름·역할·warm intro 경로·현재 접점 상태 컬럼 포함). 총 ≥ 25명 매핑

#### R5.2: 주간 아웃리치 cadence
- **Given**: R5.1 의 map 존재
- **When**: 각 스프린트 주말 종료 시점
- **Then**: `outreach-log.md` 에 해당 주 ≥ 3건의 outbound touch 기록 (채널·대상·내용·응답 상태). 누적 ≥ 12건

#### R5.3: Inbound signal 로깅
- **Given**: 이메일/LinkedIn/폼 등 inbound 채널 모니터링
- **When**: 인바운드 수신
- **Then**: `inbound-log.md` 에 `(source, acquirer, type, date, next_action)` 기록. R0.1 카운트는 type ∈ {M&A, acqui-hire, investment-with-acqui-hire-option} 인 항목만

#### R5.4: D-Day gate & Plan B 자동 트리거
- **Given**: 2026-05-18 23:59 KST 지남
- **When**: Gate checker 실행 (R0.1 조건 검증)
- **Then**: ① R0.1 만족 시 `sprint-retrospective.md`에 success 기록 + acquirer 쪽과 후속 협의 스케줄링 ② 불만족 시 D6 Plan B 자동 문서 생성: `plan-b-smaller-exit.md` 에 11번가→네이버→카카오→배민 순서로 재타겟, outreach cadence 2주 연장안 포함

## Tasks

### T1: Attribution schema migration [infra]
- **Fulfills**: R3.1, R1.1, R1.5 (+ prerequisite for R1.3, R3.2, R3.3, R4.4)
- **Depends on**: (none)
- **Scope**: `analytics.py` 테이블에 `surface`, `client_id`, `client_version`, `utm_source` 컬럼 추가. `backend.py`에 request header parsing middleware (`x-openclaw-surface` enum 정규화 + `x-openclaw-client-id` fallback). 기존 `log_event`/`record_event` 호출을 새 컬럼 포함하도록 업데이트. SQLite + Firestore 양쪽 스키마 동기화.

### T2: Public OpenAPI 3.x spec publish [vertical]
- **Fulfills**: R1.2
- **Depends on**: (none)
- **Scope**: `backend.py`에 `GET /openapi.json` + `GET /docs` 라우트 추가. OpenAPI 3.1 document는 static Python dict으로 build. public endpoints(assist/search/deeplinks/events/goldbox/best/s) 전부 문서화 + header schema(x-openclaw-*) + 예시 payload. 기존 라우팅 핸들러 수정 최소화.

### T3: WAS endpoint + 세션 집계 [vertical]
- **Fulfills**: R1.3, R1.4
- **Depends on**: T1
- **Scope**: `backend.py`에 `GET /v1/admin/was` 라우트 + bearer auth. `analytics.py`에 session aggregation(30분 idle timeout 알고리즘) 추가. Query 파라미터: window, surface. Cross-surface stitching 하지 않음 (D4).

### T4: Coupang Partners weekly reporting cron [vertical]
- **Fulfills**: R3.2
- **Depends on**: T1
- **Scope**: `economics.py`에 weekly report fetcher + upsert. `GET /v1/admin/gmv` 엔드포인트. Cloud Scheduler job 정의(09:00 KST daily). Credential은 기존 Cloud Run env 사용. 실패 시 경고만, 다음 실행에서 재시도.

### T5: Shortlink proxy GMV pipeline + attribution 강화 [vertical]
- **Fulfills**: R3.3, R3.5, R3.6, R3.7, R3.8
- **Depends on**: T1, T4
- **Scope**:
  1. `url_shortener.py` redirect handler를 확장 — `X-Forwarded-For` 첫 IP를 `client_ip`로, `User-Agent`를 `user_agent`로 이벤트에 싣기. 기존 `remote_addr`는 `proxy_ip`로 리네임.
  2. `analytics.py` 스키마 마이그레이션 — `events` 테이블에 `client_ip`, `user_agent`, `proxy_ip` 컬럼 추가 (T1이 추가한 attribution 컬럼과 별도).
  3. `economics.py`에 `(client_ip, slug)` 기준 10초 dedup 알고리즘 구현해 `effective_clicks` 산출.
  4. 주간 proxy GMV aggregation 배치 (일요일 23:00 KST) — `proxy_gmv` 테이블에 `(surface, week_start, raw_clicks, effective_clicks, estimated_basket_krw, proxy_gmv_krw)` 작성. 공식 `proxy_gmv_krw = effective_clicks × avg_basket × 0.015`.
  5. 매일 22:00 KST reconciliation job — Coupang Partners 리포트 (T4 cron과 공유) 읽어 `click_reconciliation` 테이블에 `(date_kst, our_raw_clicks, our_effective_clicks, coupang_reported_clicks, gap_ratio)` 기록. gap_ratio > 0.5 시 경고 이벤트 emit.
  6. `GET /v1/admin/gmv/proxy` + `GET /v1/admin/click-reconciliation` 엔드포인트 (bearer auth) — pitch dashboard(T6)가 소비.
  7. 최초 배포 후 24h 내 2026-04-21 baseline (raw 6, effective 3 예상, coupang 1, gap 67%) 재현 확인.

### T6: Pitch dashboard page [vertical]
- **Fulfills**: R3.4, R0.2
- **Depends on**: T3, T4, T5
- **Scope**: `backend.py`에 `GET /acquihire-pitch` (public, read-only) 라우트 + 정적 HTML 템플릿. 4 섹션 렌더(ship list · WAS chart · GMV split · brand signals). Ship list는 `ship-log.md` 읽기, brand signals은 `content-log.md` 읽기. retn.kr DNS는 post-work로 분리(Cloudflare redirect 또는 서브도메인 설정).

### T7: Claude Code skill marketplace ship [vertical]
- **Fulfills**: R2.3 (+ 부분 R2.7)
- **Depends on**: T1, T2
- **Scope**: 기존 `openclaw_skill/SKILL.md` 을 Claude Code skill marketplace 요구사항에 맞춤 (라이센스, 스크린샷, 예시 프롬프트). 마켓 제출 + 승인 추적. `ship-log.md`에 hard/soft ship 라벨 기록. Attribution header 자동 삽입 로직 skill 측에 embed.

### T8: Python CLI PyPI 패키지 [vertical]
- **Fulfills**: R2.4 (+ 부분 R2.7)
- **Depends on**: T1
- **Scope**: `bin/openclaw_shopping.py` 를 `openclaw-shopping` PyPI 패키지로 리팩터. setuptools 패키징, entrypoint 정의, semver 태그. 모든 CLI 요청에 `x-openclaw-surface=cli` + persistent client_id(UUID 생성 후 `~/.config/openclaw/client_id` 저장) 포함. 사용자 docs (`README.md`) 업데이트.

### T9: 공개 MCP 패키지 [vertical]
- **Fulfills**: R2.5 (+ 부분 R2.7)
- **Depends on**: T1, T2
- **Scope**: `public_mcp/` 신규 디렉토리 — `coupang_product_search_skill/`의 operator-only MCP를 thin wrapper로 전환(a.retn.kr 백엔드 호출, credentials 불필요). MCP registry 또는 GitHub public repo에 공개. `x-openclaw-surface=mcp` 태깅. 설치 가이드 포함.

### T10: ChatGPT GPT Store 제출 [vertical]
- **Fulfills**: R2.1 (+ 부분 R2.7)
- **Depends on**: T2
- **Scope**: GPT builder에서 Action 설정(OpenAPI spec URL = a.retn.kr/openapi.json), privacy policy 페이지 작성, 예시 프롬프트 3개, 아이콘. 제출 → under review 상태 달성. `ship-log.md`에 상태/스크린샷 기록.

### T11: OpenAI Codex integration [vertical]
- **Fulfills**: R2.2 (+ 부분 R2.7)
- **Depends on**: T2
- **Scope**: Codex custom tool schema 작성(OpenAI platform 요구사항 기반). Repository public + 설치 가이드. 외부 tester 3명 이상 확보 및 client_id 로그 확인.

### T12: OpenClaw/claw 계열 integration [vertical]
- **Fulfills**: R2.6 (+ 부분 R2.7)
- **Depends on**: T2
- **Scope**: OpenClaw skill 업데이트(routing 관련 최신 spec 반영) + claw variants(예: claw-shopping fork) 공개. PR을 관련 repos에 제출. `ship-log.md` 기록.

### T13: D-Day distribution gate 스크립트 [infra]
- **Fulfills**: R2.7
- **Depends on**: T7, T8, T9, T10, T11, T12
- **Scope**: `scripts/check_d_day_ship.py` — `ship-log.md` 파싱해 hard/soft/announcement 카운트. 조건 검증(hard ≥ 3 AND soft ≥ 3). 결과를 `sprint-gate-result.json`으로 출력. R6의 gate가 읽음.

### T14: Technical post-mortem 장문 3편 퍼블리시 [vertical]
- **Fulfills**: R4.1
- **Depends on**: T2 (OpenAPI를 인용)
- **Scope**: `docs/blog/agentic-commerce-openclaw-coupang-beta.md` 드래프트 정비 + 추가 2편 작성(토픽: "solo+multi-agent로 한국 agent-shopping 백엔드 설계", "의도 분배권 thesis 실전 적용"). retn.kr Ghost에 퍼블리시. GitHub repo/OpenAPI 링크 embed. `content-log.md`에 기록.

### T15: Build-in-public X 데일리 [vertical]
- **Fulfills**: R4.2
- **Depends on**: T3 (WAS 숫자 인용), T13 (ship 숫자 인용)
- **Scope**: 주 4회+ X 포스팅. 스프린트 WAS/ship 수치 포함. `content-log.md`에 post URL 기록. (실행은 스프린트 기간 내 지속.)

### T16: 시장론 HN + LinkedIn 장문 [vertical]
- **Fulfills**: R4.3
- **Depends on**: T14 (retn.kr 장문 크로스링크 필요)
- **Scope**: HN Show 1편 + LinkedIn 2편(> 1000자). twocents thesis 확장. `content-log.md` 기록.

### T17: Paid spend 캠페인 + utm attribution [vertical]
- **Fulfills**: R4.4
- **Depends on**: T1 (utm 파싱 필요)
- **Scope**: X 부스팅 + LinkedIn 캠페인 셋업(70/30 split). utm_source는 `x-paid-{network}-{campaign}-{variant}` naming. `spend-log.md`에 주간 지출/클릭/conversion 기록. 월 1M KRW 초과 알람.

### T18: Acquirer contact 매핑 [vertical]
- **Fulfills**: R5.1
- **Depends on**: (none)
- **Scope**: `specs/acquihire-sprint-1mo/acquirer-targets.md` — 쿠팡/네이버/카카오/11번가/배민 각 ≥ 5명. 이름·역할·warm intro 경로·상태. 사용자 개인 네트워크 인벤토리 기반.

### T19: 주간 outreach cadence [vertical]
- **Fulfills**: R5.2
- **Depends on**: T18
- **Scope**: 매주 ≥ 3건 outbound (LinkedIn DM / email / warm intro). `outreach-log.md` 기록. 스프린트 4주 × 3 = 누적 ≥ 12건.

### T20: Inbound signal 로깅 프로토콜 [vertical]
- **Fulfills**: R5.3
- **Depends on**: (none)
- **Scope**: `inbound-log.md` 템플릿 생성. 이메일/LinkedIn/폼 모니터링(수동 루틴 또는 메일 자동 포워딩). 타입 분류 규칙(M&A / acqui-hire / investment-with-acqui-hire-option = R0.1 카운트).

### T21: D-Day gate + Plan B automation [infra]
- **Fulfills**: R5.4, R0.1
- **Depends on**: T13, T18, T19, T20, T6
- **Scope**: `scripts/d_day_gate.py` — 2026-05-19 00:00 KST 시점 자동 실행. `inbound-log.md` 파싱해 R0.1 판단. 성공 → `sprint-retrospective.md` 생성. 실패 → `plan-b-smaller-exit.md` 생성(11번가→네이버→카카오→배민 순 재타겟 + 2주 연장안).

## External Dependencies

### Pre-work
- Coupang Partners API access key 유효성 확인 (COUPANG_ACCESS_KEY/SECRET_KEY 현재 상태 점검).
- OpenAI GPT Store developer account + privacy policy 문구 작성.
- Anthropic Claude Code skill marketplace 등록 계정.
- PyPI 계정 (`openclaw-shopping` 패키지 이름 선점 확인).
- Ghost CMS admin token for retn.kr (blog publishing).
- X / LinkedIn / HN 계정 active + 2FA.
- Cloud Scheduler + Cloud Run 기존 IAM role 확인 (cron 작업 추가용).
- retn.kr 도메인의 `/acquihire-pitch` path 라우팅 (Cloudflare redirect 또는 서브도메인).
- Acquirer warm intro contact 인벤토리 (사용자 네트워크) — T18 전제.
- D1 의 surface 승인 심사 일정(ChatGPT GPT/Claude Code skill marketplace/MCP registry) 사전 조사.

### Post-work
- R0.1 성공 시: acquirer 미팅 스케줄링 + NDA 검토 + data room 준비 (현재 spec 범위 외).
- R0.1 실패 시: `plan-b-smaller-exit.md` 실행 — 11번가/네이버/카카오/배민 라인 outreach 재시작 + 2주 연장 스프린트 플랜.
- book_reco 재가동 — 스프린트 후 v1 런칭 재개 (현재 70% 상태 preserve).
- 승인 대기 surface(T10/T11/T12 잔여분) 심사 통과시 hard ship 승격 및 `ship-log.md` 업데이트.
- Paid 캠페인 중 ROAS 저조한 variant 종료.
- 4 주 retrospective: D4 WAS 1.5% 전환 가정 실측 비교 + D9/D10 스키마 실사용 피드백.
