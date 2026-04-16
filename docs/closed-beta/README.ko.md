# Closed Beta Guide

이 문서는 `shopping-copilot` closed beta 참여자를 위한 사람용 안내서입니다.

## 목적

- 사용자는 쿠팡 검색/추천을 OpenClaw에서 바로 호출할 수 있습니다.
- 검색/추천/딥링크 생성은 모두 hosted backend `https://a.retn.kr`를 통해 처리됩니다.
- 따라서 사용자는 쿠팡 API 키를 직접 가질 필요가 없습니다.

## 기본 원칙

- 기본 backend는 `https://a.retn.kr` 입니다.
- beta 참여자는 `OPENCLAW_SHOPPING_API_TOKEN`만 받으면 됩니다.
- 로컬 backend는 운영자 개발용입니다. beta 사용자는 건드리지 않는 것이 맞습니다.
- stale한 `127.0.0.1` 설정이 남아 있어도 closed beta 경로에서는 `https://a.retn.kr`로 강제됩니다.

## 사용자 설정

OpenClaw 설정의 `env.vars`에 아래 두 값이 있어야 합니다.

```json
{
  "OPENCLAW_SHOPPING_BASE_URL": "https://a.retn.kr",
  "OPENCLAW_SHOPPING_API_TOKEN": "issued-per-beta-user-or-shared-beta-token"
}
```

## 추천 사용 문장

- `머리가 큰 사람도 고통 없이 쓸 수 있는 미세먼지 마스크 찾아줘`
- `대두가 써도 안 아픈 KF94 마스크 추천해줘`
- `shopping-copilot으로 쿠팡에서 AUX 선 제일 긴거 찾아줘`
- `shopping-copilot으로 가성비 러닝 양말 찾아줘. 링크도 같이 줘`
- `shopping-copilot으로 브레빌 870으로 라떼 만들 오트밀크 추천해줘. 쿠팡에서 살 수 있는 것만`

가능하면 prefix 없이도 쇼핑 요청으로 인식되게 맞춰 두었지만, 라우팅이 애매하면 `shopping-copilot으로`를 붙여 다시 요청하는 것이 가장 확실합니다.

## 기대 동작

- 추천 1~3개
- 근거와 주의점
- 바로 살 수 있는 링크
- 제휴 disclosure 문구

## 운영 주의

- beta 토큰은 외부에 공개하지 마세요.
- 링크는 `a.retn.kr` 또는 쿠팡 제휴 딥링크 형태로 나오는 것이 정상입니다.
- 같은 Discord 스레드에서 오래된 문맥이 남으면 이상한 backend 주소를 다시 언급할 수 있습니다.
새 스레드에서 다시 시도하면 대부분 정리됩니다.

## 이상 증상

- `127.0.0.1` 링크가 나온다
  - 정상 아님. 운영자에게 알려주세요.
- 검색 결과가 전혀 다른 카테고리로 튄다
  - 질의문과 응답 전문을 함께 전달해 주세요.
- 링크가 빠졌다
  - 응답 전문을 전달해 주세요. 현재 정책상 추천 시 링크는 포함되어야 합니다.
