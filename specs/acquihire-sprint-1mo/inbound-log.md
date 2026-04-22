# Inbound Signal Log — Acquihire Sprint 1mo

> Spec 참조: [`specs/acquihire-sprint-1mo/spec.md`](./spec.md) — **R5.3** (Inbound signal 로깅), **R0.1** (D-Day 2026-05-18까지 쿠팡·네이버·카카오·11번가·배민 중 1곳 이상에서 M&A / acqui-hire 인바운드 미팅 ≥ 1건 확보).
> 운영 프로토콜: [`docs/inbound-protocol.md`](../../docs/inbound-protocol.md) — 채널 모니터링, 분류, 에스컬레이션 규칙.
> 관련 파일: [`acquirer-targets.md`](./acquirer-targets.md) (연락처 맵), `outreach-log.md` (아웃바운드), `inbound-qualifying.md` (R0.1 카운트 대상 스냅샷, 필요 시 생성).
> 작성자: outreach-bd lead (단독 창업자 본인).
> 성격: **append-only 감사 로그**. 과거 행은 절대 다시 쓰지 않는다 (T21 D-Day gate가 이 파일을 읽어 R0.1 성공 여부를 판정).

---

## 0. Legend — `type` enum

| type | 정의 | R0.1 카운트? |
| --- | --- | --- |
| `M&A` | 인수/합병 논의가 명시적으로 언급된 접촉 (회사 단위 거래). | **yes** (조건부) |
| `acqui-hire` | 팀 인수 — 창업자+팀 채용 + IP/제품 이전을 전제로 한 접촉. | **yes** (조건부) |
| `investment-with-acqui-hire-option` | 전략적 acquirer의 투자 라운드인데 이사회 의석 / 우선매수권 등으로 향후 acqui-hire 옵션을 내포. (예: 쿠팡 직접 투자 + board seat) | **yes** (조건부) |
| `partnership` | 상업 제휴, 레비뉴 쉐어, 통합(integration) 논의. 발전 시 위 3종으로 재분류 가능. | no |
| `press` | 기자·미디어 취재 요청. | no |
| `recruiting` | 본인(또는 팀)에 대한 단순 채용 문의. IP 이전 없음. | no |
| `other` | 위 어디에도 맞지 않는 모든 것 (일반 CS, 스팸, 학술, 커뮤니티 질문 등). | no |

### R0.1 카운트 조건 (AND 결합)

1. `type ∈ {M&A, acqui-hire, investment-with-acqui-hire-option}`,
2. `Acquirer/Org ∈ {쿠팡, 네이버, 카카오, 11번가, 배민, 그 외 쇼핑·커머스 대기업}`,
3. 연락 주체가 해당 회사를 **공식적으로 대리**함을 스스로 밝힘 (외부 3자 추정/루머는 제외).

세 조건을 모두 충족하는 경우에만 `R0.1 counts?` 컬럼에 `yes`를 기재한다. 세부 규칙은 [`docs/inbound-protocol.md`](../../docs/inbound-protocol.md) §4 참조.

---

## 1. Inbound entries (append-only)

| Date | Source | Acquirer/Org | Contact Name | Role | Type | Summary | R0.1 counts? | Next action | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 2026-04-15 | email | (example — 포맷 예시용) | (N/A) | (N/A) | `other` | 포맷 예시 행. 스프린트 시작 이전 샘플이며 실제 inbound 아님. 첫 실제 inbound가 기록되면 이 행은 그대로 두고 아래에 append. | no | (예시 — 액션 없음) | logged |

---

## 2. 작성 규칙 (footer)

- **Append-only**: 새로운 inbound는 표 맨 아래에 행을 추가한다. 과거 행은 **절대 수정·삭제하지 않는다** (감사 추적). 분류가 바뀌면 새 행으로 update-note를 append하고 기존 행은 그대로 둔다.
- **24시간 규칙**: inbound 수신 후 24시간 이내 이 파일에 행 생성. `Next action`은 절대 비워두지 않는다.
- **R0.1 판정 보수성**: 애매할 때는 `R0.1 counts? = no`로 기록하고 `Status`에 `pending-reclassify` 메모 후 재검토한다.
- **D-Day gate (T21)**: 2026-05-19 00:00 KST 시점에 `scripts/d_day_gate.py`가 이 파일을 파싱해 `R0.1 counts? = yes` 행 수를 센다. ≥ 1 → success, 0 → Plan B 자동 문서화.
