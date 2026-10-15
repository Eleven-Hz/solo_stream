# 품질 검증 방법

자동 테스트(pytest)는 없다. AI 출력은 정답이 없어서 **같은 입력으로 돌려 보고 결과를 읽는** 방식으로 검증한다.

## 원칙

- **같은 입력으로 비교한다.** 샘플 영상·음성 + 같은 seed(라운드별 발언 봇 구성)로, 바꾼 것 하나만 다르게.
- **숫자보다 대화록.** HP/SP 언급 %, 평균 길이, 비용은 참고. 결정은 대화록에서 "스트리머 말(🎤)에 반응했나", "화면과 맞나", "지어낸 게 없나"를 읽고 한다.
- **1회 실행은 운이 섞인다.** 차이가 애매하면 seed 를 바꿔 한 번 더.
- **비용이 든다.** 실제 API 호출 — 실행 전에 범위(초·라운드 수)를 정한다. 기본 300초 = 20라운드, 몇 센트.

## 스크립트

샘플 파일(git 제외, 로컬에 있어야 함): `tests/vision/video/crosscode_video_01.mkv`, `tests/stt/audio/crosscode_audio_01.wav`.
출력은 각 폴더의 `output/`(git 제외).

| 스크립트 | 용도 |
|------|------|
| `tests/replay/run.py` | 지금 코드·설정 그대로 샘플을 라운드 단위로 재생 — 화면 분석 → 받아쓰기 → 채팅, main 과 같은 후처리 |
| `tests/replay/compare_chat.py` | run.py 결과를 고정하고 채팅 모델만 바꿔 A/B |
| `tests/stt/` | 받아쓰기 모델 정확도(정답 문장 대비) |
| `tests/vision/run.py` | 화면 분석 모델 비교(영상 프레임) |
| `tests/ui/smoke_settings_tabs.py` | UI 스모크(실제 화면 캡처) |
| `tests/ui/readme_shots.py` | README 스크린샷 생성(위젯 렌더, 가짜 키) |

### 리플레이 — 프롬프트·후처리·프로필을 바꿨을 때

```bat
venv\Scripts\python.exe tests\replay\run.py --tag before
:: (변경 후)
venv\Scripts\python.exe tests\replay\run.py --tag after
```
`output/before.txt` 와 `after.txt` 를 나란히 읽는다. 옵션: `--start/--end/--interval`, `--vision/--chat/--stt provider:model`, `--game`, `--seed`.

### 채팅 모델 A/B

```bat
venv\Scripts\python.exe tests\replay\compare_chat.py tests\replay\output\before.json ^
    openai:gpt-6-luna gemini:gemini-2.5-flash-lite claude:claude-haiku-5-5
```
표(메시지·HP/SP%·길이·토큰·지연·$/h)가 출력되고, `<tag>_compare.txt` 에 시간순으로 모델별 채팅이 나란히 적힌다.

## 대화 흐름 개선 전후 (2026-10-16)

조건: 샘플 0~300초, 15초 주기, seed 7, vision·chat 모두 gpt-6-luna, STT gpt-transcribe. `before_flow` = 개선 전 코드, `after_flow3` = 최종.

| 항목 | 전 | 후 |
|------|------|------|
| 발화 → 채팅 생성 지연(문장 끝 기준 평균) | 13.0초 | **8.4초** |
| 발화 즉시 반응 라운드 | – | 18회 (채팅 호출 20 → 38) |
| 다른 봇 닉네임 부른 채팅 | 0 | 16 |
| 말버릇("새벽두부", "라면") | 5 | 1 |
| 채팅 비용 | $0.14/h | $0.26/h |

- 샘플 음성은 쉬지 않고 해설하는 녹음이라 '말 끝나고 1.5초' 조건이 잘 안 맞는다 — 띄엄띄엄 말하는 실제 방송에선 지연이 더 짧다(가짜 클라이언트 스모크: 마지막 말 2초 뒤 반응 라운드).
- 받아쓰기 고유명사 힌트: "가을에 몰락"→"가을의 몰락", "초보자 안고"→"초보자 항구", "시체로 되집"→"시트론의 집", "사파 여섯 맥"→"사파이어 산맥".
- 대화록에서 확인한 것: "여기가 누구 집이었더라?"에 봇들이 시트론/홈스테드로 서로 반박, 인사·계획("홈스테드 가보시죠")에 직접 반응. 남은 문제: 사실 오답("홈스테드는 레아 집"), 가끔 지어낸 이름.
- 측정은 `run.py` 요약의 `speech_latency_avg`·`speech_rounds`·`bot_mentions`.

## 앱에서 직접 확인할 것

리플레이로는 안 보이는 것: 실제 마이크 입력, 오버레이 표시 타이밍, 설정 변경의 실시간 반영, exe 빌드의 `%APPDATA%` 사본 문제.
- 로그: `logs/session_*.log`(프로세스), `logs/chat_*.log`(라운드 블록 — 프롬프트·응답·토큰).
- 설정 '사용량' 탭의 실제 사용 수치.
