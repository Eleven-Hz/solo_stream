"""API 오류 문구 분류. 재시도해도 소용없는 오류(키·모델·한도)를 사용자가 읽을 말로 바꾼다.

SDK 를 import 하지 않는 순수 함수라 AI·STT 프로세스 어디서나 쓴다.
"""


def is_fatal_api_error(msg: str) -> bool:
    """사용자가 설정을 고치기 전엔 계속 실패할 오류면 True (키 오류·모델 없음·권한·크레딧)."""
    m = (msg or "").lower()
    return any(s in m for s in (
        "401", "403", "404", "api_key_invalid", "invalid api key", "incorrect api key",
        "authentication", "permission", "not_found", "not found", "no longer available",
        "does not exist", "insufficient_quota",
    ))


def describe_api_error(msg: str) -> str:
    """오류 원문을 채팅창에 띄울 짧은 안내로 바꾼다."""
    m = (msg or "").lower()
    if any(s in m for s in ("401", "api_key_invalid", "invalid api key", "incorrect api key", "authentication")):
        return "API 키가 올바르지 않아요 — 설정 'AI 모델 · 키'에서 확인하세요."
    if any(s in m for s in ("404", "not_found", "not found", "no longer available", "does not exist")):
        return "이 모델을 쓸 수 없어요(서비스 종료·접근 제한) — 설정 'AI 모델 · 키'에서 다른 모델을 고르세요."
    if "insufficient_quota" in m or "billing" in m:
        return "API 크레딧이 없어요 — 해당 서비스의 결제 상태를 확인하세요."
    if "403" in m or "permission" in m:
        return "이 키로는 접근 권한이 없어요 — 키와 프로젝트 설정을 확인하세요."
    if "429" in m or "resource_exhausted" in m or "rate limit" in m:
        return "요청 한도를 넘었어요 — 잠시 뒤 다시 시도됩니다."
    return (msg or "알 수 없는 오류").strip().splitlines()[0][:100]
