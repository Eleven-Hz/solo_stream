# solo_stream 위키

코드만 봐서는 알 수 없는 것 — **실측 결과, 외부 API 특성, 결정의 이유, 검증 방법** — 을 모아 둔다.

| 문서 | 내용 |
|------|------|
| [models-and-providers.md](models-and-providers.md) | 모델별 실측(품질·지연·토큰·비용), 단가, provider API 특성 |
| [decisions.md](decisions.md) | 주요 설계 결정과 그 이유 (한 줄씩, 날짜순) |
| [evaluation.md](evaluation.md) | 품질을 검증하는 방법과 평가 스크립트 사용법 |
| [game-profile-sources.md](game-profile-sources.md) | 게임 프로필을 정확하게 만드는 법 (게임 데이터·위키·구독 채팅) |

## 쓰는 규칙

- **구조·코드 규칙은 `AGENTS.md`**, **실측·결정·외부 사실은 여기**. 같은 내용을 두 곳에 쓰지 않는다 — 필요하면 링크.
- 실측 수치에는 **날짜와 조건**(모델 버전, 샘플, 라운드 수)을 같이 적는다. 모델·단가는 자주 바뀐다.
- 틀린 것으로 밝혀지면 지우지 말고 고친 이유를 한 줄 남긴다(같은 실수를 반복하지 않게).
- 날짜별 작업 기록은 `dev_history/`(로컬 전용)이고, 여기는 주제별로 오래 남길 것만.
