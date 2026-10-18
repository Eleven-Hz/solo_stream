<!-- Parent: ../AGENTS.md -->
<!-- Generated: 2026-05-24 | Updated: 2026-05-24 -->

# docs

## Purpose
프로젝트 설계 문서 (한국어). 기획서 v2.0이 전체 비전·아키텍처·로드맵·비용 모델의 단일 진실. 기능 단위 추가 계획서를 별도 파일로 보관.

## Key Files
| File | Description |
|------|-------------|
| `solo_stream_기획서.md` | v2.0 기획서. 핵심 기능(Vision/Chat/STT/Overlay), 페르소나 시스템, 멀티프로세스 아키텍처, AI 모델 추상화 원칙(`src/ai/model_client.py`가 유일 진입점), 비용 설계, Phase 1/2/3 로드맵, config.yaml 전체 스키마. **새 기능 설계 시 이 문서의 원칙(특히 §4.2 AI 클라이언트 추상화)을 위배하지 말 것.** |
| `wiki/` | 실측·결정·검증 방법 위키(주제별, 오래 남길 것). 규칙은 `wiki/README.md` |
| `음성인식_기능_추가_계획.md` | Phase 2 STT 도입 계획서 — silero-vad + Gemini Audio 백엔드 선택 근거, VAD 파라미터, 봇 호출(@봇이름) 시나리오 |

## For AI Agents

### Working In This Directory
- 새 기능 도입 시 짧은 한국어 계획서를 한 파일 추가하고, 본 기획서의 관련 절을 한 줄 링크로 보강한다.
- 본 기획서는 비전 문서이므로 구현 세부가 바뀌어도 자주 갱신하지 않는다 — 대신 변경 사실은 `dev_history/<날짜>.md`에 남긴다.
- 코드 변경 PR 설명에 docs 갱신이 필요한지 한 줄로 명시.

### Common Patterns
- 표는 한국어 헤더 + GitHub-flavored markdown 표.
- 코드 예시는 yaml 또는 python 블록.

## Dependencies

### Internal
- 코드 어디서도 import하지 않음 — 사람용 문서 전용.

<!-- MANUAL: -->
