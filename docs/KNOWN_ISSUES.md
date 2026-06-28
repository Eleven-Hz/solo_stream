# Known Issues (알려진 문제)

프로젝트에서 알려진 이슈와 현재 대응 상태를 기록합니다.

---

## 1. 채팅 생성 시 503 UNAVAILABLE (Gemini 서버 혼잡)

### 증상
채팅 생성 중 로그에 다음과 같은 오류가 찍히고, 해당 라운드의 봇 채팅이 비어서
화면에 채팅이 안 뜹니다.

```
[solo_stream.ai] [ERROR] 채팅 생성 실패: 503 UNAVAILABLE. {'error': {'code': 503,
'message': 'This model is currently experiencing high demand. ...', 'status': 'UNAVAILABLE'}}
```

### 원인
- 기본 채팅 모델인 `gemini-2.5-flash` 는 무료/공용 티어에서 트래픽이 몰리는
  시간대에 503(서버 혼잡)을 자주 반환합니다.
- 현재 `GeminiClient._call_with_retry()` 는 **429/RESOURCE_EXHAUSTED(쿼터 초과)만
  재시도**하고, **503/UNAVAILABLE 은 재시도하지 않습니다.**
  (`src/ai/model_client.py` 의 `_call_with_retry` 참고)
- 따라서 503이 뜨면 그 라운드는 `generate_chats()` 의 `except Exception` 에 잡혀
  빈 리스트를 반환하고, 그 회차 채팅만 누락됩니다. (앱이 죽지는 않음)

### 영향 범위
- **채팅 생성**: 해당 라운드 채팅 누락 (다음 라운드는 정상 시도).
- **Vision 분석**(`analyze_screenshot`)도 동일하게 503은 재시도 안 함 →
  실패 시 `VISION_ERROR_MARKER` 로 처리되어 "화면 정보 없음"으로 진행.
- **게임 프로필 생성**(`game_profile_loader._search`)은 별개로 이미 503 재시도가
  적용되어 있음 (최대 5회, 4→8→16→30s 백오프). 채팅/Vision 경로와 혼동 주의.

### 현재 대응
- 앱 차원의 자동 재시도는 **아직 없음** (이 문서로 먼저 기록).
- 사용자가 취할 수 있는 임시 조치:
  1. 잠시 후 자동으로 다음 라운드에 다시 시도되므로 기다린다.
  2. 503이 지속되면 **AI 모델 탭에서 채팅 모델을 변경**한다
     (예: `gemini-2.5-flash-lite` 또는 `gemini-2.0-flash`, 혹은 Claude provider).
  3. 유료 티어/별도 API 키 사용 시 503 빈도가 크게 줄어든다.

### 향후 개선 후보 (미구현)
- `_call_with_retry` 의 재시도 조건에 `503`/`UNAVAILABLE`/`500`/`overloaded` 등
  일시적 오류를 추가하고 지수 백오프 적용 (게임 프로필 생성의 `_search` 와 동일한
  방식). 단, 채팅 생성은 라운드 주기에 묶여 있어 백오프가 너무 길면 채팅이
  지연되므로, 짧은 재시도(예: 1~2회, 1~2s)로 제한하는 설계 검토 필요.
- 반복 503 시 사용자에게 "채팅 모델을 바꿔보라"는 안내를 오버레이에 1회 노출.

---

## 2. 재부팅 시 프로필이 무조건 크로스코드로 지정됨 ✅ 수정됨

### 증상
- 게임을 자동 감지하고 **프로필 적용**을 누른 뒤 프로그램을 껐다 켜면, 마지막에
  선택/적용한 프로필이 아니라 **항상 크로스코드(CrossCode) 프로필**로 지정됨.
- 그 결과 스텔라리스를 하는데 봇들이 크로스코드 이야기를 하는 게임 오인식이 발생
  (아래 4번 항목과 직접 연결됨).

### 확정 원인 (분석 완료)
자동 감지 오작동이 아니라 **마지막 선택을 디스크에 저장하지 않아 config 기본값으로
되돌아가는 것**이다.

- 재시작 시 초기 게임명은 **항상** `config["capture"]["target_window"]` 에서만
  추출된다 (`src/main.py:280`).
  ```python
  initial_game = config["capture"].get("target_window") or ""
  game_name = _extract_game_name(initial_game)
  ```
- 그런데 `config.yaml:13` 의 `target_window` 기본값이 하드코딩된
  `CrossCode Min v1.4.2-4` → `_extract_game_name` → **"CrossCode"**.
- UI에서 "창 변경 적용"·"프로필 적용"을 누르면 `target_window`/`game_name` 은
  `_settings_queue.put(...)` 로 **런타임 큐에만** 전달된다
  (`src/ui/overlay.py:619, 1777, 1785, 1788`). `src/main.py` 의 while 루프
  (`:65`, `:379`)는 이를 받아 **런타임 변수만 갱신**하고 **config.yaml 에는 저장하지
  않는다.**
- 대조군: vision/chat 모델·STT 설정은 적용 시 `_save_config_vision_provider()` /
  `_save_config_chat_model()` 등으로 **yaml.dump 디스크 저장**된다. 즉 게임 창/프로필
  선택만 이 저장 호출이 빠져 있다.
- 결과: 다음 실행에서 `:280` 이 다시 하드코딩 기본값을 읽어 항상 크로스코드로 폴백.

추가로, "캡처 대상 창"(`target_window`)과 "프로필 게임명"(`game_name`)은 별개 상태로
관리되므로(프로필 적용은 `game_name` 만 큐에 넣음), 복원 시 **두 값을 함께** 다뤄야 한다.

### 해결 방향 (채택: 별도 last_session 상태파일)
`config.yaml`(사용자 기본설정/번들 시딩 대상)을 오염시키지 않기 위해, 마지막 세션
선택만 분리 저장한다.

- `user_data_dir()/last_session.yaml`(혹은 .json)에 마지막 `target_window` 와 적용
  `game_name` 을 저장.
- "창 변경 적용"·"프로필 적용" 시 큐 전송과 함께 이 파일에 기록.
- 시작 시(`main.py`) `target_window`/초기 게임명을 **last_session → 없으면 config 기본값**
  순으로 해석.
- 자동 감지 결과보다 사용자가 명시적으로 "프로필 적용"한 선택을 우선.

### 적용된 수정 (구현 완료)
- `src/utils/paths.py`: `load_last_session()` / `save_last_session(target_window=, game_name=)`
  헬퍼 추가. 상태는 `user_data_dir()/last_session.yaml` 에 UTF-8(yaml)로 병합 저장.
  손상/저장 실패는 조용히 무시하고 기본값으로 폴백(앱 동작을 막지 않음).
- `src/main.py` `main()`: `load_config()` 직후 last_session 으로
  `config["capture"]["target_window"]` 및 `config["capture"]["game_name"]` 을 덮어씀
  → 캡처 프로세스와 AI 프로세스가 모두 같은 config 를 받아 한 곳에서 복원됨.
- `src/main.py` `ai_process`: `config["capture"]["game_name"]`(복원된 명시 선택)이 있으면
  `target_window` 추출보다 **우선** 사용.
- `src/ui/overlay.py`: 창/프로필을 적용하는 6개 큐 전송 지점 모두에 `save_last_session(...)`
  추가 (메인 탭 `_apply_window`/`_apply_profile`/`_regenerate_profile`,
  시작 탭 `_start_apply_window`/`_start_regenerate_profile`).
- `config.yaml` 은 건드리지 않음(번들 시딩 대상 보존).
- 검증: last_session 라운드트립(빈 로드→저장→병합) + 한국어 UTF-8 보존 + 3개 파일
  `py_compile` 통과 확인.

---

## 3. 캡처된 게임명과 적용된 프로필 이름이 크게 다를 때 경고 없음 ✅ 수정됨

### 증상
- 화면에서 감지한 게임명과 실제 적용된 프로필 이름이 많이 달라도 사용자가 알 수
  없음 (2번 버그를 사용자가 놓치는 원인).

### 적용된 수정 (구현 완료)
- `src/main.py` 에 `_normalize_for_match()` / `_profile_name_mismatch()` 휴리스틱 추가.
  캡처 창에서 추출한 게임명(`_extract_game_name(target_window)`)과 적용된 프로필
  이름(`profile["name"]`)을 정규화(괄호 한국어 병기 제거·기호 제거·소문자) 후 비교.
  한쪽이 다른 쪽을 포함하면 일치로 보고, 아니면 `difflib` 유사도 < 0.5 일 때 불일치로 판정.
- `ai_process._notify_profile_loaded()` 가 불일치 시 "시스템" 채팅으로 경고 노출
  (예: 창은 Stellaris 인데 프로필이 CrossCode 인 경우).
- AI 프로세스가 `settings_queue` 의 `target_window` 변경을 추적하도록 분기 추가
  (기존엔 캡처 프로세스만 처리했음) → 경고가 최신 창 기준으로 동작.
- 오탐 방지: 창 정보 없음/`Unknown Game`/부분 포함(예: "Hades II" ↔ "Hades")은 경고 안 함.
- 검증: 8개 케이스(핵심 버그 시나리오 경고 + 정상/병기/부분일치 무경고) 전부 통과,
  `py_compile` 통과.

### 향후 개선 후보 (미구현)
- 감지명 ↔ 프로필명 유사도가 낮으면 오버레이/설정 탭에 **경고 표시**.

---

## 4. 봇 대화 품질 이슈

### 4-1. 같은 말 반복
- 할 말이 없을 때 일부 봇이 **페르소나 예제 문구**를 그대로 재발화하는 것으로 추정.
- 개선 후보: 예제는 톤 가이드로만 쓰고 직접 인용 억제, 직전 발화와의 중복 필터.

### 4-2. "렉 걸린다" 과다 발화
- 대부분의 봇이 "렉 걸린다"는 말을 함. 특히 ADHD 페르소나가 많이 함.
- 개선 후보: 특정 클리셰 표현 빈도 제한.

### 4-3. STT 오인식에 봇이 과반응
- "**기술**"을 "**술**"로만 알아들으면 봇들끼리 술 이야기로 빠짐.
- STT 정확도 문제 + 봇이 오인식 텍스트에 그대로 끌려가는 이중 문제.
- 개선 후보: STT 신뢰도 낮은 토큰 처리, 봇이 단편적 단어에 과몰입하지 않도록 프롬프트 보정.

### 4-4. 방랑객 페르소나가 다른 스트리머 이름을 모름
- 다른 봇/스트리머를 지칭하지 못함 (페르소나 컨텍스트 부족).
- 개선 후보: 현재 활성 페르소나 목록을 각 봇 컨텍스트에 주입.

---

## 5. 화면 표시 / UI 이슈

### 5-1. 과거 대화가 일부만 보임
- 먼 과거 대화가 화면에 안 보임. 아웃풋 토큰 제한과도 연관 가능.
- 개선 후보: 채팅 히스토리 표시 범위/스크롤 확대.

### 5-2. 화면(상황) 묘사가 너무 짧음
- Vision 캡션이 빈약해 봇에게 충분한 정보를 주지 못함.
- 개선 후보: 화면 묘사 프롬프트/토큰 한도 조정.

### 5-3. (긍정) STT 결과가 채팅창에 바로 보임
- 내가 한 말과 STT 해석 결과를 즉시 확인 가능 — **좋은 평가, 유지/강화**.

---

## 6. 기능 제안

### 6-1. 실시간 사용량 분석
- 현재는 가정치 기반 예상 가격만 표시 → **실제 사용량 실시간 집계** 필요.

### 6-2. (긍정) 고사양 모델로 디테일한 프로필 생성
- 이미 일부 반영됨 — 좋은 평가.

---

## 7. 운영 / 배포

### 7-1. `%APPDATA%` 업데이트 시 기존 파일 처리 정책
- 업데이트 시 `%APPDATA%`의 기존 파일(프로필/설정 등)을 어떻게 보존/마이그레이션할지
  정책 확인 필요.
