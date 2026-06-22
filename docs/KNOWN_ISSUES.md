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
