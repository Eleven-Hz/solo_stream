<!-- Parent: ../AGENTS.md -->

# replay

## Purpose
샘플 영상(`tests/vision/video/crosscode_video_01.mkv`)과 음성(`tests/stt/audio/crosscode_audio_01.wav`)을 **앱과 같은 코드 경로**로 라운드 단위 재생해, 프롬프트·후처리·모델·게임 프로필 변경 전후의 채팅을 같은 입력으로 비교한다.

## Key Files
| File | Description |
|------|-------------|
| `run.py` | 리플레이 1회 — 화면 분석 → 받아쓰기(VAD 세그먼트) → 채팅. `output/<tag>.json`(원자료) + `.txt`(시간순 대화록) |
| `compare_chat.py` | `run.py` 결과의 화면 분석·발화·발언 봇을 고정하고 채팅 모델만 바꿔 A/B. 요약 표 + `<tag>_compare.txt` |
| `common.py` | 공용: 프레임/받아쓰기, `ChatRound`(main 의 후처리 재현), 라운드 발언 봇 seed, 지표, 대화록 |

## For AI Agents

### Working In This Directory
- `ChatRound.run` 은 `src/main.py` ai_process 의 채팅 후처리(발언자 외 드롭 → `_drop_repeat_chats` → `limit_round_topics` → 소재 기록)와 발화 규칙(`MAX_SPEECH_ROUNDS`)을 그대로 따른다. main 의 순서·규칙을 바꾸면 여기도 같이 바꾼다 — 안 그러면 리플레이가 실제 앱과 달라진다.
- 실제 API 호출이라 비용이 든다. 기본 0~300초·15초 = 20라운드(몇 센트). 짧게 볼 땐 `--start/--end`.
- 판단은 숫자보다 대화록(`.txt`)을 읽어서 — 검증 원칙은 `docs/wiki/evaluation.md`.
- 산출물 `output/` 은 git 제외.
