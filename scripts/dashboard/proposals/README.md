# 검토 요청(상신) 폴더

Claude 는 현황판 데이터(`../data/*.json`)를 직접 고치지 않는다. 바꾸고 싶으면 여기에 요청을 올리고,
사용자가 현황판(`open-dashboard.bat`) **검토 요청** 탭에서 승인하면 그때 반영된다. 반려하면 사유만 남는다.

```
proposals/<id>/proposal.json   요청 내용
proposals/<id>/*.png           스크린샷(proposal.json 의 screenshots 에 적은 것만 보인다)
```

`<id>` 는 영문·숫자·`.`·`_`·`-` 만. 날짜로 시작하면 정렬이 쉽다: `2026-10-27-event-hype`.

## proposal.json

```json
{
  "title": "게임 이벤트에 채팅이 몰림 — Vision 이벤트를 흥분도에 연결",
  "created": "2026-10-27T09:00:00+09:00",
  "author": "Claude",
  "status": "pending",
  "summary": "무엇을 왜 바꿨는지. 여러 줄 가능.",
  "commits": ["abc1234 feat(chat): Vision 이벤트로 흥분도 올림"],
  "files": ["src/chat/hype.py", "src/main.py"],
  "changes": [
    {"dataset": "features", "op": "update", "key": "eventhype",
     "from": {"status": "계획"}, "set": {"status": "완료", "detail": "이벤트 줄 → hype, 잔불 2라운드"},
     "why": "리플레이에서 보스전 라운드 채팅 1.8배"}
  ],
  "tests": [
    {"name": "리플레이 전후 비교", "result": "통과", "command": "venv\\Scripts\\python tests/replay/run.py", "detail": "보스전 샘플 40라운드"},
    {"name": "실제 앱", "result": "건너뜀", "detail": "사용자가 run.bat 으로 확인 필요"}
  ],
  "screenshots": [{"file": "replay.png", "caption": "리플레이 리포트"}],
  "notes": "남은 일, 사용자가 실제 앱에서 확인해 줄 것"
}
```

- `status`: 올릴 때는 늘 `pending`. `approved`·`rejected` 와 `decision`(시각·의견)은 현황판이 적는다. 손으로 바꾸지 않는다.
- `changes`: 현황판 데이터에서 바뀌는 줄. 없으면 빈 배열(코드만 바뀐 보고).
  - `dataset`: `features`(키 `id`) · `stages`(키 `order`) · `workflows`(키 `id`, 단계는 `steps` 통째로 바꾼다)
  - `op: "update"` — `set` 의 칸을 바꾼다. **`from` 에 지금 값을 꼭 적는다.** 승인할 때 지금 값과 다르면 충돌로 막힌다(다른 요청이 먼저 바꾼 경우).
  - `op: "add"` — `row` 에 새 줄 전체. `op: "remove"` — 그 줄을 지운다.
- `tests[].result`: `통과` · `실패` · `건너뜀`. 실패도 숨기지 않고 적는다. 테스트가 없으면 현황판이 경고를 띄운다.
- 코드에서 바로 읽는 것(알려진 문제 표·봇·게임 프로필·모델·커밋 등)은 승인과 상관없이 코드대로 보인다. 그 변경은 `commits`·`files`·스크린샷으로 보고만 한다.
