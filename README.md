# Solo Stream

혼자 게임할 때 실제 스트리머처럼 **AI 시청자 봇들이 실시간으로 채팅을 치는** 몰입형 솔로 게이밍 오버레이입니다.

화면 캡처 → Vision 분석 → 페르소나 기반 채팅 생성 → 투명 오버레이 표시 파이프라인을 멀티프로세스로 구동합니다. 마이크 음성 인식(STT)으로 스트리머의 혼잣말도 채팅 컨텍스트에 반영됩니다.

> Windows 전용입니다 (`pywin32`/`win32gui` 의존). UI·봇 페르소나·프롬프트는 한국어 기본입니다.

## 주요 기능

- **AI 시청자 봇**: 게임 화면을 분석해 페르소나(겜린이·고인물킹·도네요정 등)별 말투로 채팅
- **음성 반영(STT)**: silero-vad로 발화 구간을 잡고 받아쓰기해 봇이 반응 (봇 호명·질문 응답)
- **게임 프로필 자동 생성**: 플레이 중인 창을 감지해 게임별 용어·공략 컨텍스트를 자동 구성
- **투명 오버레이**: 게임 위에 겹쳐 뜨는 채팅 창

## 사용하는 AI Provider

기능 영역별로 provider를 골라 쓸 수 있습니다 (UI 설정에서 런타임 교체).

| 기능 | 선택 가능한 provider | 기본값 |
|------|----------------------|--------|
| 채팅 / Vision | `gemini`, `claude` | gemini-2.5-flash |
| 음성 인식(STT) | `gemini`, `openai` | gemini-2.5-flash |
| 게임 프로필 생성 | `gemini` (고정 — Google Search 그라운딩) | gemini-2.5-flash |

## 설치 (소스 실행)

Python 3.10 + Windows 환경 기준입니다.

```bat
setup.bat       :: venv 생성 + requirements.txt 설치
```

또는 수동으로:

```bat
python -m venv venv
venv\Scripts\pip install -r requirements.txt
```

## API 키 설정

`%APPDATA%\SoloStream\.env.local` (소스 실행 시에는 프로젝트 루트의 `.env.local`)에 키를 넣습니다. `.env.example`을 참고하세요.

```
GEMINI_API_KEY=your_gemini_key_here
CLAUDE_API_KEY=your_claude_key_here    # 채팅/Vision을 claude로 쓸 때
OPENAI_API_KEY=your_openai_key_here    # STT를 openai로 쓸 때
```

키는 앱 내 설정 패널의 **[API 키 저장]** 버튼으로도 입력할 수 있습니다. 키가 없으면 앱은 죽지 않고 첫 실행 온보딩 화면으로 진입합니다.

> 최소 `GEMINI_API_KEY` 하나면 기본 구성(gemini)으로 동작합니다.

## 실행

```bat
run.bat         :: = python -m src.main
```

## 설정

`config.yaml`에서 provider/model, 캡처 주기, 채팅 빈도, 오버레이 UI, STT 파라미터 등을 조정합니다. 봇 페르소나는 `personas/*.yaml`에 추가할 수 있습니다 (자세한 사용법은 동봉된 `사용설명.txt` 참고).

## 빌드된 실행 파일 (설치 불필요)

Python 설치 없이 바로 쓰려면 [Releases](../../releases)에서 `SoloStream.zip`을 받으세요. 압축을 풀고 `SoloStream.exe`를 실행하면 됩니다.

- 무결성 확인: 함께 받은 `SoloStream.zip.sha256`의 해시와 받은 zip의 SHA256을 대조하세요.
- `BUILD_INFO.txt`에 빌드된 커밋·의존성 버전·산출물 해시가 기록돼 있습니다.

## 빌드 (개발자용)

```bat
venv\Scripts\pip install -r requirements-build.txt
build.bat
```

`dist\SoloStream\`에 onedir 포터블 빌드 + `BUILD_INFO.txt` + `dist\SoloStream.zip`(+`.sha256`)이 생성됩니다. 정식 배포본은 커밋된 clean tree에서 빌드해야 `BUILD_INFO.txt`의 출처가 신뢰 가능합니다.

## 라이선스

[MIT](LICENSE)
