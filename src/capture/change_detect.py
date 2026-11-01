"""SSIM 기반 화면 변화 감지 모듈.

화면 전체 평균만 보면 대화창 글자나 퍼즐 한 칸처럼 작은 변화가 묻힌다(전체 SSIM 0.99).
그래서 화면을 격자로 나눠 가장 많이 바뀐 칸의 SSIM 도 함께 본다.
"""
import io
import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity as ssim

GRID_COLS = 6
GRID_ROWS = 4
# 가장 많이 바뀐 칸의 SSIM 이 이 값 미만이면 변화로 본다.
# 실측(2026-11): 대사 변경 0.80, 퍼즐 칸 이동 0.83, 메뉴 선택 이동 0.92, 같은 화면 재인코딩 1.00.
DEFAULT_BLOCK_THRESHOLD = 0.95


def _to_grayscale_array(jpeg_bytes: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
    return np.array(img)


def change_scores(prev_bytes: bytes, curr_bytes: bytes) -> tuple[float, float]:
    """(화면 전체 SSIM, 가장 많이 바뀐 칸의 SSIM)."""
    prev_arr = _to_grayscale_array(prev_bytes)
    curr_arr = _to_grayscale_array(curr_bytes)

    # 크기가 다르면 작은 쪽에 맞춘다
    if prev_arr.shape != curr_arr.shape:
        h, w = min(prev_arr.shape[0], curr_arr.shape[0]), min(prev_arr.shape[1], curr_arr.shape[1])
        prev_arr = prev_arr[:h, :w]
        curr_arr = curr_arr[:h, :w]

    score, ssim_map = ssim(prev_arr, curr_arr, data_range=255, full=True)
    h, w = ssim_map.shape
    worst = min(
        float(ssim_map[h * r // GRID_ROWS:h * (r + 1) // GRID_ROWS,
                       w * c // GRID_COLS:w * (c + 1) // GRID_COLS].mean())
        for r in range(GRID_ROWS) for c in range(GRID_COLS)
    )
    return float(score), worst


def has_significant_change(
    prev_bytes: bytes,
    curr_bytes: bytes,
    threshold: float = 0.92,
    block_threshold: float = DEFAULT_BLOCK_THRESHOLD,
) -> bool:
    """전체 SSIM < threshold 이거나 가장 많이 바뀐 칸의 SSIM < block_threshold 면 True."""
    score, worst = change_scores(prev_bytes, curr_bytes)
    return score < threshold or worst < block_threshold
