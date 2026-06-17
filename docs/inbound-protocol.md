# Inbound Signal Protocol — Acquihire Sprint 1mo

> Spec 참조: [`specs/acquihire-sprint-1mo/spec.md`](../specs/acquihire-sprint-1mo/spec.md) — **R5.3** (Inbound signal 로깅), **R0.1** (D-Day 2026-05-18 성공 기준), **D6** (Plan B 규칙).
> 연결 파일: [`specs/acquihire-sprint-1mo/inbound-log.md`](../specs/acquihire-sprint-1mo/inbound-log.md) (append-only 로그), [`specs/acquihire-sprint-1mo/acquirer-targets.md`](../specs/acquihire-sprint-1mo/acquirer-targets.md) (연락처 맵).
> 운영자: outreach-bd lead (단독 창업자 본인).
> 목적: "inbound 한 건이 이메일로 도착함" → "`inbound-log.md`에 감사 가능한 행으로 기록됨"까지의 무손실 파이프라인 정의.

---

## 1. 모니터링 채널 (where inbound can arrive)

다음 채널 전부가 inbound 유입 지점이다. 누락 없이 매일 1회 이상 점검한다.

- **Email (Gmail primary)** — 주 수신함.
  - 필터 쿼리: `from:(@coupang.com OR @navercorp.com OR @kakaocorp.com OR @11st.co.kr OR @woowahan.com OR @skgroup.co.kr)` → 라벨 `inbound/acquirer-candidate`로 자동 분류.
  - 보조 쿼리: `subject:(인수 OR acqui-hire OR M&A OR acquisition OR investment OR 투자 OR 제휴 OR partnership)` → 라벨 `inbound/keyword-hit`.
  - 공개 문의 주소 (`retn.kr` 안내 페이지에 노출되는 주소 포함) 전부를 Gmail로 포워딩.
- **LinkedIn DMs / InMail** — 창업자 본인 프로필 + 회사 페이지(존재 시) 양쪽.
  - 주 1회 이상 inbox 전수 점검. 모바일 알림은 스팸 섞임이 심하므로 데스크톱에서 재확인.
- **X (Twitter) DMs + mentions** — build-in-public(angle ①) 채널이라 진짜 acquirer 시그널이 올 확률이 있다.
  - `@` 멘션 중 `acqui/acquisition/인수/M&A` 키워드 매치를 수동 스캔.
- **retn.kr 문의 폼 / GitHub issues** — 공개 레포(OpenClaw skill 등) issue 및 웹 문의 폼.
  - 폼이 없는 시점에는 `hi@retn.kr`(또는 동등 이메일)을 대체 창구로 사용하고 채널 1(Email)에서 흡수.
- **Warm intros via 개인 네트워크** — 공통 지인 전달 포함. 구두·카톡·메신저로 들어오는 경우까지 포함한다.
  - 비디지털 경로는 수신 당일 본인이 직접 `inbound-log.md`에 행을 만들어야 한다 (자동 포집 불가).

각 채널 점검 주기 최저 기준:
- 이메일: 업무일 3회 (오전/오후/저녁).
- LinkedIn/X: 일 1회.
- GitHub/폼: 주 3회.
- Warm intro: 발생 즉시 (실시간).

---

## 2. 라우팅 규칙 (routing within 24h)

- inbound 확인 시점부터 **24시간 이내**에 [`inbound-log.md`](../specs/acquihire-sprint-1mo/inbound-log.md) 표 하단에 행을 append 한다.
- 필수 컬럼: `Date | Source | Acquirer/Org | Contact Name | Role | Type | Summary | R0.1 counts? | Next action | Status`.
- `Next action`은 **절대 비워두지 않는다**. 명확한 판단이 어려우면 `triage: 48h 내 재검토`처럼 최소 시한과 함께 기재한다.
- 기존 행은 수정하지 않는다 (append-only). 재분류가 필요하면 새 행을 추가하고 `Summary`에 "supersedes row dated YYYY-MM-DD"를 명시한다.

---

## 3. 분류 규칙 — `type`

다음 기준으로 엄격히 분류한다. 중복 해당 시 위에서 먼저 만족하는 것을 택한다.

- **`M&A`** — 회사 전체 인수/합병 논의가 명시적으로 언급된 경우. "acquisition", "합병", "인수 논의 차원" 등 거래 의도가 드러나야 함.
- **`acqui-hire`** — 팀/창업자 중심의 채용성 인수. 제품/IP 이전 + 팀 합류가 구조의 핵심. "팀 합류 + 기술 가져오는 형태" 류의 표현.
- **`investment-with-acqui-hire-option`** — 전략 투자 제안이지만 이사회 의석·우선매수권·합류 옵션 등 **장래 acqui-hire 전환 장치**가 붙은 경우. 전형 예: 쿠팡 직접 투자 + board seat + option pool 재조정 요구.
- **`partnership`** — 상업 제휴, revenue share, API 통합(integration), co-marketing. **R0.1에 카운트하지 않는다.** 단, 후속 대화에서 위 3종으로 전개되면 새 행으로 기록하여 카운트 대상으로 전환.
- **`press`** — 기자·미디어 취재 문의.
- **`recruiting`** — 인재 채용 문의 (IP 이전 없음). 본인 개인을 스카우트하는 채용은 여기.
- **`other`** — 이상 어디에도 해당하지 않는 모든 접촉 (일반 CS 문의, 스팸, 학술 협업, 커뮤니티 질문 등).

---

## 4. R0.1 카운트 규칙 (explicit)

한 행이 R0.1에 카운트되려면 다음 세 조건을 **동시에** 충족해야 한다:

1. **Type gate** — `type ∈ {M&A, acqui-hire, investment-with-acqui-hire-option}`.
2. **Acquirer gate** — `Acquirer/Org` 가 `{쿠팡, 네이버, 카카오, 11번가, 배민, 그 외 쇼핑·커머스 대기업}` 중 하나. 사이즈·영역 경계가 애매한 회사는 §6 주간 리뷰에서 재판정.
3. **Representation gate** — 연락 주체가 해당 회사를 **공식적으로 대리**한다고 스스로 밝힘. 증빙: 회사 이메일 도메인, LinkedIn 프로필의 현 소속, 명함/서명, 또는 warm intro 제공자의 명시적 확인. 제3자가 전해듣거나 추정해서 전달한 신호는 카운트하지 않는다 (Status에 `third-party-rumor`로 남기고 `type=other` 처리).

세 조건 중 하나라도 불충족이면 `R0.1 counts? = no`. 충족 시 `yes`와 함께 §5 에스컬레이션 프로세스를 실행한다.

경계 사례 처리:
- 쇼핑·커머스 카테고리 경계: 네이버/카카오 내 비쇼핑 사업부에서 오는 접촉도 커머스 관련성만 명시되면 카운트 대상으로 간주 (예: 네이버 AI 본부가 네이버쇼핑 핏 전제로 접촉).
- 익명/추상 표현: "우리 쪽에서 관심" 식의 소속 불명확 메시지는 회신으로 소속·직함을 확인할 때까지 `pending-reclassify` 유지.
- `investment-with-acqui-hire-option` 판단: "option"이 메시지·통화에서 **명시적으로** 언급됐거나 term sheet/텀시트 상에 실제 조항으로 붙어야 함. 단순 투자 제안은 `partnership`에 준하여 카운트 대상 아님.

---

## 5. 에스컬레이션 규칙 (qualifying inbound 도착 시)

R0.1 카운트 대상(§4 세 조건 충족)이 식별되면:

1. **6시간 이내 수신 확인 회신** — acquirer 입장에서의 "응답 속도 기대치"에 부합. 내용은 "미팅 가능 시간 3개 제안 + 기술/재무 간단 티저 1문단" 수준이면 충분. 24시간 넘기면 신호 경직 위험.
2. **`inbound-qualifying.md` 스냅샷 생성/업데이트** — qualifying 건만 모은 별도 파일 (R0.1 카운트 후보 집합). 행을 `inbound-log.md`에서 복사해 붙이되, 요약을 2-3문장으로 확장해 pitch data room에서 바로 참조 가능한 형태로 유지. 파일이 없으면 이 시점에 새로 만든다.
3. **`acquirer-targets.md` 행 동기화** — 연락 주체가 이미 매핑돼 있으면 해당 행의 `Status` = `inbound-received`, `Last touch` 갱신. 미매핑이면 새 행을 추가(회사 섹션 하단)하고 warm-intro 경로 공란이어도 일단 기록.
4. **NDA/데이터룸 준비 지시** — 실제 미팅 확정 단계에 도달하면 spec §비전략(post-work) 영역으로 이관 (현재 스프린트 범위 외지만 트리거 시점은 여기에서).

비카운트성 inbound(예: `partnership`, `press`)는 일반 답신 템플릿만 회신하고 §5 추가 절차 실행 안 함.

---

## 6. 주간 리뷰 리추얼 (weekly review)

- **매주 일요일** 저녁, 지난 7일간 `inbound-log.md`에 추가된 모든 행을 다시 읽는다.
- 재분류 조건:
  - 신규 정보로 `type` 판단이 달라진 경우 → 새 행을 append (원 행은 보존). `Summary`에 근거/출처 명시.
  - `pending-reclassify` 상태 행은 이 리뷰에서 모두 종결(확정 또는 `other`로 결론).
- 카운트 변동 보고: 주간 리뷰 직후 현재 `R0.1 counts? = yes` 행 수를 한 줄 메모로 자기 점검 (예: "wk3: qualifying = 0"). 필요 시 `outreach-log.md`에도 메모.

---

## 7. D-Day gate 참조 (T21 연결)

- 2026-05-19 00:00 KST에 `scripts/d_day_gate.py` (T21)가 [`inbound-log.md`](../specs/acquihire-sprint-1mo/inbound-log.md)를 자동 파싱한다.
- 판정 로직:
  - `R0.1 counts? = yes` 행 수 ≥ 1 → success. `sprint-retrospective.md` 생성 + 후속 미팅 스케줄링.
  - = 0 → 실패. D6 Plan B 자동 문서화 (`plan-b-smaller-exit.md`) — 11번가 → 네이버 → 카카오 → 배민 순 재타겟 + 2주 연장안.
- 본 프로토콜의 모든 규칙은 이 gate가 깨끗하게 파싱 가능하도록 **표 포맷과 `yes`/`no` 소문자**를 강제한다. 스크립트 호환성 깨는 표 포맷 변경 금지.

---

## 8. 변경 이력

- `2026-04-18` (T20): 초기 프로토콜 정의. 모니터링 채널 5종, 24h 라우팅, `type` 7종 enum, R0.1 3-조건 AND 카운트, 6h 에스컬레이션, 주간 리뷰, T21 연결 명시.
