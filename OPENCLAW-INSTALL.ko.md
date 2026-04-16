# OpenClaw 설치 / 사용 가이드

이 저장소에는 `shopping-copilot` 스킬 패키지가 들어 있습니다.

## 중요한 점

- `https://github.com/retention-corp/coupang_partners` URL만 붙여 넣었을 때 OpenClaw가 **무조건 자동 설치**한다고 현재 이 저장소만으로 보장할 수는 없습니다.
- 설치 이후 OpenClaw가 **자동으로 온보딩 멘트**를 말하는 것도 현재 이 저장소만으로 보장할 수는 없습니다.
- 다만, 설치 후에는 `shopping-copilot`이 prefix 없이도 쇼핑 요청을 더 잘 잡도록 스킬 문구를 강화해 두었습니다.

## 가장 짧은 권장 방법

OpenClaw에게 아래 문장을 그대로 붙여 넣으세요.

```text
이 GitHub 저장소를 보고 shopping-copilot 스킬을 설치하거나 사용할 수 있게 세팅해줘: https://github.com/retention-corp/coupang_partners

설치가 끝나면 내가 이렇게 말하면 된다고 안내해줘:
"머리가 큰 사람도 고통 없이 쓸 수 있는 미세먼지 마스크 찾아줘"
```

## 설치 후 권장 사용 문장

- `머리가 큰 사람도 고통 없이 쓸 수 있는 미세먼지 마스크 찾아줘`
- `대두가 써도 안 아픈 KF94 마스크 추천해줘`
- `쿠팡에서 AUX 선 제일 긴 거 찾아줘`
- `가성비 러닝 양말 추천해줘. 링크도 같이 줘`

## 안 잡히면 이렇게 말하기

라우팅이 애매하면 가장 확실한 fallback은 아래처럼 prefix를 붙이는 것입니다.

- `shopping-copilot으로 머리가 큰 사람도 고통 없이 쓸 수 있는 미세먼지 마스크 찾아줘`
- `shopping-copilot으로 대두가 써도 안 아픈 KF94 마스크 추천해줘`

## 운영자 메모

- public 경로는 `https://a.retn.kr` 입니다.
- 일반 사용자는 `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY`를 알 필요가 없습니다.
- 공개 사용자는 tokenless public path를 쓰고, 운영자 전용 경로만 internal/operator bearer token을 사용합니다.
