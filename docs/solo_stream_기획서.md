# Solo Stream — 기획서 v2.0

> 혼자 게임할 때 실제 스트리머처럼 AI 시청자 봇들이 채팅을 치는 **몰입형 솔로 게이밍 경험** 툴.
> 장르 무관 범용 설계, RPG/오픈월드에서 특히 잘 동작하는 것을 목표로 한다.

---

## 1. 프로젝트 개요

### 1.1 배경 및 목적

혼자 게임을 플레이할 때 스트리밍 방송의 채팅창처럼 AI 봇들이 실시간으로 게임 상황에 반응하는 채팅을 생성하여 몰입감을 높인다. 게임 화면을 AI가 분석하고, 각기 다른 페르소나를 가진 봇들이 자연스러운 한국어 채팅을 만들어내는 것이 핵심이다.

### 1.2 핵심 가치

- **몰입감**: 실제 시청자가 있는 것 같은 채팅 경험
- **다양성**: 봇마다 고유한 성격, 말투, 반응 패턴
- **경제성**: 월 $5~10 수준의 API 비용으로 운영 가능
- **확장성**: 모델 교체, 페르소나 커스텀, 오픈소스 배포를 고려한 설계

### 1.3 대상 사용자

혼자 게임을 즐기면서도 방송 느낌의 상호작용을 원하는 게이머.

---

## 2. 핵심 기능

### 2.1 스크린샷 분석 (Vision)

- 설정된 주기(기본 15초)마다 게임 화면을 캡처한다.
- SSIM(구조 유사도) 기반으로 이전 프레임과 비교하여 변화가 없으면 API 호출을 스킵한다. 기본 스킵률 목표는 50~60%이다.
- Gemini Vision API로 현재 게임 상황을 텍스트로 요약한다.
- 창모드 캡처를 우선 지원하고, 풀스크린은 차후 DXGI/Windows Graphics Capture API로 확장한다.

### 2.2 AI 채팅 봇

- 10~15개의 봇이 각자의 페르소나에 따라 한국어 채팅을 생성한다.
- **배치 프롬프트** 방식으로, 1회 API 호출에 여러 봇(기본 5명)의 응답을 JSON 배열로 한 번에 받는다.
- 봇별 시스템 프롬프트에 이름, 말투 스타일, 감정 상태, 관계도 등을 주입한다.
- 랜덤 딜레이와 봇별 채팅 빈도 차이로 동시 출력을 방지하여 자연스러운 타이밍을 구현한다.
- **대화 기록 참조**: 최근 5~10개 채팅을 슬라이딩 윈도우로 유지하여 배치 프롬프트에 포함한다. 봇들이 이전 맥락을 알고 대화를 이어가거나, 다른 봇의 채팅에 자연스럽게 반응할 수 있다. 윈도우 크기는 `config.yaml`의 `chat.context_window`로 조절한다.

### 2.3 음성 인식 (STT) — Phase 2

- 마이크 입력을 상시 수신하되, VAD(Voice Activity Detection)로 실제 발화 구간만 감지하여 API로 전송한다.
- 스트리머의 음성 컨텍스트를 봇 채팅 생성에 반영한다.
- Gemini Audio API 또는 로컬 Whisper 중 비용과 성능을 비교하여 선택한다.
- **특정 봇 호출 (@봇이름)**: STT로 인식한 텍스트에서 봇 이름이 감지되면, 해당 봇이 페르소나에 맞는 우선 응답을 생성한다. 예: "고인물킹 여기 어떻게 가?" → 고인물킹 봇이 공략 팁으로 응답.

### 2.4 투명 오버레이 채팅창

- PyQt6 기반 투명 창으로 게임 화면 위에 띄운다.
- 자유 배치가 가능하며 드래그로 위치를 이동할 수 있다.
- 미니멀 스타일로 `닉네임: 메시지` 형태로 표시한다.
- Always-on-top으로 게임 위에 유지된다.

---

## 3. 페르소나 시스템

### 3.1 봇 설정값

각 봇은 아래 속성을 가진다.

| 속성 | 설명 | 예시 |
|---|---|---|
| name | 봇 닉네임 | 겜린이, 고인물킹, 트롤마스터 |
| style | 말투 스타일 | 반말 막내팬, 존댓말 고인물, 트롤러 |
| emotion | 감정 상태 (세션 내 변화) | 평온, 신남, 열받음, 실망 |
| relationship | 스트리머와의 관계 | 팬, 안티, 뉴비, 고인물 |
| frequency | 채팅 빈도 | high(수다쟁이), medium, low(가끔) |
| triggers | 반응 트리거 조건 | 사망 시, 보스전, 레벨업 등 |

### 3.2 파일 구조

페르소나는 YAML 파일로 관리하며, `personas/` 디렉토리에 저장한다.

```yaml
# personas/default_bots.yaml
bots:
  - name: "겜린이"
    style: "반말, 이모티콘 많이 사용, 초보 질문"
    emotion: "신남"
    relationship: "뉴비팬"
    frequency: "high"
    triggers:
      - type: "death"
        reaction: "ㅋㅋㅋㅋ 또 죽었어??"
      - type: "boss_clear"
        reaction: "와 미쳤다 진짜!!"

  - name: "고인물킹"
    style: "존댓말, 공략 팁 제공, 쿨한 반응"
    emotion: "평온"
    relationship: "고인물"
    frequency: "low"
    triggers:
      - type: "death"
        reaction: "거기는 왼쪽으로 가야 합니다."
```

---

## 4. 아키텍처

### 4.1 멀티프로세스 구조

세 개의 프로세스가 Python `multiprocessing.Queue`로 통신한다.

| 프로세스 | 역할 | 주요 모듈 |
|---|---|---|
| Capture Process | 화면 캡처 + SSIM 변화 감지 | `mss`, `scikit-image` |
| AI Process | Vision 분석 + 채팅 생성 + 감정 관리 | `google-generativeai` |
| UI Process | 투명 오버레이 렌더링 | `PyQt6` |

데이터 흐름은 다음과 같다.

```
Capture Process                AI Process                   UI Process
┌──────────────┐              ┌──────────────────┐         ┌─────────────────┐
│ mss 캡처     │   Queue #1   │ Vision 분석      │  Queue  │ PyQt6 오버레이  │
│ SSIM 비교    │ ──────────▶  │ 배치 채팅 생성   │ ──#2──▶ │ 채팅 렌더링     │
│ 15초 주기    │  (이미지)    │ 감정 상태 관리   │ (채팅)  │ 드래그 이동     │
└──────────────┘              │ STT 처리 (Ph.2)  │         └─────────────────┘
                              └──────────────────┘
```

### 4.2 AI 클라이언트 추상화

모든 AI API 호출은 `src/ai/model_client.py` 한 파일에 집중한다. 다른 모듈은 이 파일이 제공하는 인터페이스만 사용하며, `google.generativeai` 등의 SDK를 직접 import하지 않는다.

이 설계의 이유는 다음과 같다.

- AI 모델의 지원 주기가 매우 짧다 (예: Gemini 2.0 Flash, 2026년 6월 종료).
- 모델 deprecated 시 `model_client.py`와 `config.yaml`만 수정하면 전체 코드가 동작한다.
- 향후 다른 Provider 추가 시 같은 인터페이스로 구현체를 교체할 수 있다.

```python
# src/ai/model_client.py — 유일한 API 접점
class GeminiClient:
    def __init__(self, config: dict): ...
    def analyze_screenshot(self, image: bytes) -> str: ...
    def generate_chats(self, context: str, bots: list[dict]) -> list[dict]: ...
    def transcribe_audio(self, audio: bytes) -> str: ...  # Phase 2
```

```yaml
# config.yaml — 모델 설정
ai:
  vision_model: "gemini-2.5-flash-lite"
  chat_model: "gemini-2.5-flash-lite"
  api_key_env: "GEMINI_API_KEY"
```

### 4.3 설정 파일 구조

설정과 페르소나를 분리한다. 모두 YAML 형식이다.

| 파일 | 역할 | 주요 내용 |
|---|---|---|
| `config.yaml` | 앱 전체 설정 | API 키 환경변수명, 모델명, 캡처 주기, 스킵 임계값, 배치 크기 |
| `personas/*.yaml` | 봇 페르소나 | 봇별 이름, 말투, 감정, 빈도, 트리거 |

---

## 5. 기술 스택

| 영역 | 기술 | 비고 |
|---|---|---|
| 언어 | Python 3.11+ | |
| 화면 캡처 | mss | 창모드 우선. 빠르고 가벼움 |
| 변화 감지 | scikit-image (SSIM) | 픽셀 diff 대비 안정적 |
| AI API | google-generativeai | Gemini 전용. model_client.py에서만 사용 |
| 오버레이 UI | PyQt6 | 투명 창, Always-on-top, 드래그 |
| 프로세스 통신 | multiprocessing.Queue | 캡처 ↔ AI ↔ UI 간 데이터 전달 |
| 설정 관리 | PyYAML | config.yaml + personas/*.yaml |
| 패키징 (Phase 3) | PyInstaller | 단일 실행 파일 배포 |

### 5.1 AI 모델 선택

| 용도 | 모델 | 가격 ($/1M tokens) | 비고 |
|---|---|---|---|
| 화면 분석 (Vision) | gemini-2.5-flash-lite | in $0.10 / out $0.40 | 최저가, Vision 지원 |
| 채팅 생성 (Text) | gemini-2.5-flash-lite | in $0.10 / out $0.40 | 배치 프롬프트로 비용 절감 |
| 음성 인식 (STT) | gemini-2.5-flash-lite | audio in $0.30 | Phase 2에서 적용 |

품질이 부족할 경우 gemini-2.5-flash(in $0.30 / out $2.50)로 업그레이드 가능하다.

---

## 6. 비용 설계

### 6.1 예산

- 목표: 월 **$5 이하**
- 허용 상한: 월 **$10**

### 6.2 비용 절감 전략

| 전략 | 절감 효과 | 구현 복잡도 |
|---|---|---|
| SSIM 변화 감지 스킵 (50~60%) | Vision 호출 50~60% 감소 | 낮음 |
| 배치 프롬프트 (5봇/1콜) | API 호출 80% 감소 | 낮음 |
| VAD 기반 STT (Phase 2) | STT 비용 75% 감소 | 중간 |
| Free tier 활용 (개발/테스트) | 비용 $0 | 없음 |

### 6.3 예상 월간 비용 (gemini-2.5-flash-lite 기준)

플레이 패턴: 하루 2시간, 월 25일 (총 50시간).

| 항목 | 조건 | 예상 비용 |
|---|---|---|
| Vision | 15초 주기, 스킵 50% | ~$0.80 |
| 채팅 생성 | 봇 10명, 배치 5명/콜 | ~$1.20 |
| STT (Phase 2) | VAD 모드, 15% 발화 | ~$0.50 |
| **합계** | | **~$2.50/월** |

비용 시뮬레이터를 통해 본인의 플레이 패턴에 맞는 정확한 비용을 계산할 수 있다.

---

## 7. 리얼리티 설계 원칙

1. **상황 인지**: Vision 분석 결과를 봇 프롬프트에 포함하여 실제 게임 맥락을 알고 반응하게 한다.
2. **감정 반응**: 스트리머가 죽거나 보스를 클리어하면 봇 감정 상태를 업데이트한다.
3. **타이밍 자연스럽게**: 랜덤 딜레이(1~8초) + 봇별 빈도 차이로 동시 채팅을 방지한다.
4. **말투 다양성**: 봇마다 시스템 프롬프트를 분리하고, 어휘, 이모티콘, 줄임말 스타일을 다르게 지정한다.

---

## 8. 로드맵

### Phase 1 — MVP (예상 2~3주)

캡처 + Vision 분석 + 채팅 생성 + 오버레이 UI까지 구현한다.

| 주차 | 작업 | 산출물 |
|---|---|---|
| 1주차 | 프로젝트 초기화, 폴더 구조, 설정 파일 스키마, 의존성 설정 | 뼈대 코드 |
| | mss 기반 창모드 캡처 모듈 구현 | 캡처 동작 확인 |
| | SSIM 변화 감지 + 스킵 로직 구현 | 콘솔 로그로 감지/스킵 확인 |
| 2주차 | model_client.py 작성 (Vision + 채팅 생성) | API 연동 확인 |
| | 배치 프롬프트 설계 + 페르소나 로딩 | 콘솔에 봇 채팅 출력 |
| | 타이밍 오케스트레이터 (랜덤 딜레이) | 자연스러운 출력 간격 |
| 3주차 | PyQt6 투명 오버레이 UI | 독립 창 동작 확인 |
| | 멀티프로세스 연결 (Queue 기반) | 캡처→AI→UI 파이프라인 |
| | 통합 테스트 + 버그 수정 | **MVP 완성** |

### Phase 2 — 리얼리티 강화 (예상 2주)

| 작업 | 설명 |
|---|---|
| STT 연동 | VAD + Gemini Audio (또는 로컬 Whisper) |
| 감정 변화 시스템 | 게임 이벤트에 따른 봇 감정 상태 전이 |
| 봇 간 상호작용 | A봇이 B봇을 멘션하는 기능 |
| 채팅 타이밍 고도화 | 게임 이벤트 직후 반응 급증 등 |

### Phase 3 — 커스터마이징 & 배포 (예상 2~3주)

| 작업 | 설명 |
|---|---|
| 페르소나 GUI 편집기 | 웹 기반 또는 PyQt 기반 편집 UI |
| 패키징 | PyInstaller로 단일 실행 파일 생성 |
| 문서화 | README, 설치 가이드, 페르소나 작성 가이드 |
| 오픈소스 배포 | GitHub 공개, 라이선스 선택, CI 설정 |

---

## 9. 폴더 구조

```
solo-stream/
├── config.yaml                  # 앱 설정 (모델, 캡처 주기, 배치 크기 등)
├── personas/
│   ├── default_bots.yaml        # 기본 봇 페르소나 세트
│   └── custom_example.yaml      # 커스텀 페르소나 예시
├── src/
│   ├── main.py                  # 엔트리포인트, 멀티프로세스 시작
│   ├── ai/
│   │   ├── __init__.py
│   │   └── model_client.py      # ★ 유일한 AI API 접점
│   ├── capture/
│   │   ├── __init__.py
│   │   ├── screen.py            # mss 기반 화면 캡처
│   │   └── change_detect.py     # SSIM 변화 감지
│   ├── chat/
│   │   ├── __init__.py
│   │   ├── orchestrator.py      # 봇별 타이밍 스케줄링
│   │   ├── emotion.py           # 감정 상태 관리 (Phase 2)
│   │   └── persona_loader.py    # YAML 페르소나 로딩
│   ├── ui/
│   │   ├── __init__.py
│   │   └── overlay.py           # PyQt6 투명 오버레이
│   └── utils/
│       ├── __init__.py
│       ├── config_loader.py     # config.yaml 로딩
│       └── logger.py            # 로깅 설정
├── requirements.txt
├── .env.example                 # GEMINI_API_KEY=your_key_here
├── LICENSE
└── README.md
```

---

## 10. 열린 질문

향후 개발 과정에서 결정할 사항들이다.

| 질문 | 영향 범위 | 결정 시점 |
|---|---|---|
| 봇 수 최적값 (10명 vs 15명) | API 비용, 채팅 밀도 | Phase 1 테스트 후 |
| 화면 변화 감지 SSIM 임계값 | Vision 호출 빈도, 비용 | Phase 1 튜닝 시 |
| STT를 Gemini Audio vs 로컬 Whisper | 비용, 지연시간, GPU 의존성 | Phase 2 시작 시 |
| 봇끼리 서로 반응하는 빈도 | 자연스러움 vs 노이즈 | Phase 2 테스트 후 |
| 풀스크린 캡처 방식 (DXGI vs WGC) | 호환성, 성능 | Phase 1 이후 |
| 오픈소스 라이선스 종류 | 배포 조건 | Phase 3 시작 시 |
| 오버레이를 PyQt6 유지 vs 웹 기반 전환 | 배포 용이성, 의존성 무게 | Phase 3 시작 시 |

---

## 부록 A. config.yaml 전체 스키마

```yaml
# Solo Stream 설정 파일

ai:
  vision_model: "gemini-2.5-flash-lite"     # Vision 분석용 모델
  chat_model: "gemini-2.5-flash-lite"        # 채팅 생성용 모델
  stt_model: "gemini-2.5-flash-lite"         # STT용 모델 (Phase 2)
  api_key_env: "GEMINI_API_KEY"              # API 키를 읽어올 환경변수명

capture:
  interval_seconds: 15                       # 캡처 주기 (초)
  ssim_threshold: 0.92                       # SSIM 유사도 임계값 (이상이면 스킵)
  target_window: null                        # 특정 윈도우 타이틀 (null이면 전체 화면)
  resize_width: 1024                         # API 전송 전 리사이즈 너비

chat:
  bot_count: 10                              # 활성 봇 수
  batch_size: 5                              # 1회 API 호출당 봇 수
  context_window: 10                         # 프롬프트에 포함할 최근 채팅 수 (5~10 권장)
  delay_min: 1.0                             # 최소 출력 딜레이 (초)
  delay_max: 8.0                             # 최대 출력 딜레이 (초)
  persona_dir: "personas"                    # 페르소나 YAML 디렉토리 경로

ui:
  opacity: 0.85                              # 오버레이 배경 투명도
  max_messages: 50                           # 채팅창에 표시할 최대 메시지 수
  font_size: 14                              # 채팅 폰트 크기
  width: 360                                 # 오버레이 창 너비
  height: 500                                # 오버레이 창 높이

stt:                                         # Phase 2
  enabled: false
  mode: "vad"                                # "vad" 또는 "always"
  vad_threshold: 0.5                         # VAD 감도 (0~1)
```

---

*이 기획서는 Solo Stream 프로젝트의 구체적인 구현 방향을 정리한 문서이다. Kiro 또는 GStack에서 spec 문서로 변환하여 사용할 수 있다.*
