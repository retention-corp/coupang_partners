# Deep Interview Insights: 오픈클로 외 제품 오퍼링 확장 & 수익 확대
> Date: 2026-04-18
> Rounds: 10 (hard cap)
> Final Ambiguity Score: 0.26 ⚠️
> Topic: "오픈클로 이외에도 제품 오퍼링을 좀 더 넓혀서 돈을 더 많이 벌 수 있는 플랜"

## Core Problem (재정의됨)
"월 1천만원 벌기"가 아니라 — **Retention Inc가 쿠팡(혹은 유사 acquirer)에게 acqui-hire 당할 만큼 빠르게 "AI-augmented growth hacker" 브랜드 + 한국 agent-shopping intent routing layer 포지션을 점유하는 방법.**

사용자가 인터뷰 중 공개한 진짜 목표는 "쿠팡에 회사를 파는 것"이며, 마지막 라운드에서 "1개월 안에 시장 다 먹고 AI-augmented growth hacker로 이름 날려서 Retention Inc acqui-hire"로 실행 프레임을 구체화함.

## Key Insights & Decisions
- 진짜 북극성: **쿠팡 M&A / Retention Inc acqui-hire**. 월 1천만원은 proxy 지표일 뿐.
- Moat 정의 (참고: `https://twocents.xyz/p/two-cents-90-flights-of-thought-part`): **"한국 agent-shopping 의도 분배권(intent distribution rights) 선점"** — OpenClaw 모델처럼 기존 플랫폼 보완적(complementary) 아키텍처. Doubao Phone류의 toll-extractor 포지션은 차단당함.
- 성공 KPI: 한국 agent shopping 세션의 **80%+ 가 본인 레이어 통과**.
- ICP: "AI 에이전트 쓰는 × 쿠팡에서 쇼핑하는" 교집합 유저. 현재로선 1st mover (K-skill은 같은 공간 존재).
- Unit economics 계산 거부 — "많이·자주 쓰이는 제품 포트폴리오" 우선. 측정은 raw usage.
- 실행 자원: **solo + multi-agent orchestration** (1인 → 3-5인분 가정).
- 타임라인: **1개월 내 "뚜렷한 성과"** (Post-R10 clarification — literal "시장 장악"이 아니라 measurable progress signal이 목표). 80% 세션 점유는 장기 aspirational KPI.
- 1개월 성공 기준 (4개 stack, 모두 충족 목표): ① **바이럴 브랜드** (AI-augmented growth hacker 컨텐츠 — X/LinkedIn 스레드, HN Top, 한국 테크 미디어 피쳐) ② **배포 수폭** (N개 에이전트 서피스에 MCP/스킬 배포 + 사용 로그 증거) ③ **유저 traction** (본인 서피스 통과 GMV 또는 실사용 MAU) ④ **M&A signal** (인바운드 미팅/인수 문의 1건 이상 — 쿠팡/네이버/카카오 라인).
- 경쟁자 벤치마크(월 1억 쿠팡 자동화 회사)는 SEO/유튜브 플레이로, 본인 게임과 다른 카테고리임을 인터뷰 중 확인.
- 레버리지 수단: CLI + MCP + 다양한 에이전트 서피스에 "widely 깔아놓기".

## Defined Requirements
- 주요 에이전트 서피스(Claude Code skills / ChatGPT GPTs / Cursor / Perplexity / 라이너 / 뤼튼 / 네이버 Cue / 카카오 ASK / LG 솔라 등)에 shopping MCP·CLI·스킬을 widely 배포.
- "AI-augmented growth hacker" 개인 브랜드 자산 빌드 — 콘텐츠·레퍼런스·크레드의 구체 매체 선정 필요.
- Retention Inc 법인 포지셔닝: "AI × 커머스 intent routing" 카테고리의 de facto.
- 쇼핑 외 vertical 확장(book_reco 등) — 단, 모두 "의도 라우팅" thesis 안에 들어가야 의미.
- acquirer 위협(leverage) 확보: 라우팅이 쿠팡뿐 아니라 Naver/11번가/배민 등으로도 흘러갈 수 있다는 옵션 — 그래야 쿠팡이 "파트너십 말고 인수"를 선택.

## Identified Risks & Failure Modes
- **~~타임라인 부정합~~** (해소됨): Post-R10에서 사용자가 명시 — 1개월 목표는 "뚜렷한 성과"(4-stack signal)이고 80%는 장기 KPI. 이 둘은 더 이상 충돌 아님.
- **복제 위험**: 쿠팡 내부 엔지니어 3명이 2주에 복제 가능한 수준이면 moat 없음. 참고 글 thesis의 "구조적 포지션" 실제 구현 메커니즘 미완.
- **경쟁자 존재**: NomaDamas K-skill (`coupang-product-search` MCP)이 이미 같은 공간. 차별화 메커니즘 미정.
- **Speed vs Structure 내부 긴장**: R6 답변("빠른 스케일업")은 참고 글 thesis("의도 분배권 선점")와 층위가 다름. R7에서 정렬됐으나 R10 답변에서 다시 speed + 브랜드 프레임으로 회귀 — 실제 실행에서 어느 쪽에 시간 비중을 둘지 미결.
- **Acquirer 인센티브 역전**: 80% 점유 시 쿠팡은 "사는 것"보다 "top placement 수수료 우대"로 해결 가능. M&A 강제를 위해서는 **Naver/11번가 라우팅 위협 옵션**이 필요하지만 플랜에 명시 미포함.
- **카테고리 특성 충돌**: 쇼핑은 본질적으로 주 1-2회 활동 — "많이·자주 쓰이는 daily 제품"으로 만들기 위한 구체 vertical(가격 알림·재고 알림·선물 추천·반복 구매 등) 미정.
- **브랜드 vs 프로덕트**: "AI-augmented growth hacker로 이름 날리기"(개인 브랜드 레버)와 "80% 라우팅 레이어 선점"(프로덕트 레버)은 레버리지 메커니즘이 다름. 시간 배분 결정 미완.
- **Plan B 공백**: R9·R10에서 답이 없음 — 1-3개월 안에 signal이 안 오면 어떻게 할지 미결. 데드라인을 놓쳤을 때 "조금만 더" 질질 끌 위험.

## Open Questions & Unknowns
- "1개월 안에 시장 다 먹는다"는 literal 목표인가, 아니면 "1개월 안에 bold launch 해서 6-12개월 모멘텀 만든다"인가?
- Acquirer universe가 쿠팡 단독인가, 네이버/카카오/11번가/배민/글로벌 AI 회사도 포함인가?
- "AI-augmented growth hacker" 개인 브랜드의 구체 매체·콘텐츠 전략 (블로그 retn.kr? 유튜브? 오픈소스 레포? 케이스 스터디? X? 링크드인?).
- Solo + multi-agent로 실제 월간 배포 가능한 에이전트 서피스 pace (2개? 5개? 10개?).
- Plan B — 1-3개월 내 acqui-hire signal 없을 때 다음 카드 (미응답).
- 쿠팡이 "사야 한다"고 느끼게 만들 "경쟁사 라우팅 위협"을 플랜에 포함시킬 것인가.
- 수익 모델이 전부 Coupang Partners 제휴인가, 아니면 다각화(book vertical 제휴 수수료, 광고, SaaS)인가.

## Clarity Assessment
**Ambiguity Score: 0.26** ⚠️
- Goal Clarity: 0.88 (40%) — acqui-hire 목표 선명
- Constraint Clarity: 0.55 (30%) — solo + 멀티에이전트 + 1개월 명시됐으나 Plan B·실행 pace 공백
- Success Criteria: 0.75 (30%) — 80% 세션 KPI 명확, 하지만 "1개월"과의 정합성 미해결

**Maturity: Forming** — 방향은 드러났으나 핵심 의사결정 3개(① 1개월 타임라인의 literal 여부, ② Plan B, ③ 쿠팡이 사야만 하는 구조적 이유의 구체 메커니즘)가 미결. 플랜 수립으로 넘어가기 전에 최소 ①, ②는 더 잡혀야 실행 가능한 플랜이 도출됨.

## Reference
- 본인 인용 글: `https://twocents.xyz/p/two-cents-90-flights-of-thought-part` — "의도 분배권(intent distribution rights)" 논지
- 경쟁자 참고: `https://github.com/NomaDamas/k-skill/blob/main/docs/features/coupang-product-search.md`
- 현재 자산: `https://a.retn.kr` (Cloud Run `a-retn-shortener`), OpenClaw shopping copilot skill, coupang product search MCP, book_reco vertical
