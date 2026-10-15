# 게임 프로필을 정확하게 만드는 법

프로필(`game_profiles/<게임>.yaml`)은 채팅·화면 분석 프롬프트에 매 라운드 들어간다. **이름이 화면에 뜨는 글자와 같아야** 봇이 상황을 맞게 말하고, 분량이 곧 비용이다(CrossCode 13.4KB → 9KB 로 줄여 채팅 입력 ~800토큰 절약).

## 출처 우선순위

1. **게임 데이터 파일** — 가장 정확. 현지화된 게임은 공식 번역이 데이터에 들어 있다.
2. **게임 안의 공식 텍스트**(줄거리 요약·지역 설명 등).
3. **위키**(나무위키 등) — 커뮤니티 평판·밈. 데이터로 확인 안 되는 밈은 넣지 않는다.
4. **구독 채팅 결과**(설정의 '프롬프트 복사') — 다른 게임의 기본 경로. 결과는 1~3으로 검증할 수 있으면 검증.

## CrossCode 사례 (2026-10-15)

게임 경로: `D:\SteamGame\steamapps\common\CrossCode\assets\data` (Steam). 공식 한국어 = `ko_KR`.

| 정보 | 위치 | 비고 |
|------|------|------|
| 지역 28곳 이름·설명 | `database.json` → `areas` | `name.ko_KR`, `description.ko_KR` |
| 적·보스 276종 | `database.json` → `enemies` | `name`, `species`, `area`, `boss: true` |
| 장(챕터) 이름 | `database.json` → `chapters` | |
| 공식 줄거리 | `database.json` → `lore` (STORY) | cc_mode_claude 위키에 전문 정리 |
| 전투 기술 90개 | `players/lea.json` → `actions.<속성>.<종류>_SPECIAL<n>_<A/B>.name` | |
| 캐릭터 이름 | `characters/main/*.json` → `name` | |
| 시스템 용어 | `lang/sc/gui.ko_KR.json` (`labels.combat`, `labels.menu…`) | 열기/냉기/전기/파동, 회로, 원술사 … |

- ko_KR 문자열 끝의 `<<A<<[CHANGED …]` 같은 번역 메모는 잘라낸다(`re.sub(r"<<.*$", "", s)`).
- **공식 번역이 "이상해 보여도" 그대로 쓴다** — '고슴됴치'(Hedgehag)는 오타가 아니라 공식 말장난. 반대로 자동 생성 프로필의 '루키 하버'·'분지 요새'·'전투 아츠'는 오역이었다(공식: 초보자 항구·유역의 아성·전투 기술).
- 같은 타일셋을 쓰는 지역(가을의 부상/몰락)은 `vision_focus` 에 구분 단서를 적는다(연못·유적·점프대·동굴).
- 참고 자료: `../cc_mode_claude/.omc/wiki/`(게임 분석 위키 — 줄거리·지역·던전 실측).
- 온라인: crosscode.fandom.com 은 자동 접근이 막혀 있다(402). TV Tropes 도 403. 나무위키는 읽힘.

## 스포일러

반전·엔딩은 넣지 않는다. 봇이 말해 버린다. 대신 chat_tips 에 "스포 금지 분위기"를 한 줄.

## 반영

- 저장소 기본: `game_profiles/<게임>.yaml`(개발 모드는 프로젝트 루트가 데이터 폴더).
- 배포(exe) 사용자 사본: `%APPDATA%\SoloStream\game_profiles\` 가 우선 — 기본 파일을 고쳐도 사본이 있으면 그게 읽힌다. 설정 '파일 불러오기'로 덮거나 사본을 교체.
