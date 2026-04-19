"""SSIM 기반 화면 변화 감지 모듈."""
import io
import numpy as np
from PIL import Image
from skimage.metrics import structural_similarity as ssim


def _to_grayscale_array(jpeg_bytes: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(jpeg_bytes)).convert("L")
    return np.array(img)


def has_significant_change(
    prev_bytes: bytes,
    curr_bytes: bytes,
    threshold: float = 0.92,
) -> bool:
    """
    두 JPEG 이미지를 SSIM으로 비교.
    SSIM < threshold이면 유의미한 변화 있음 → True 반환.
    """
    prev_arr = _to_grayscale_array(prev_bytes)
    curr_arr = _to_grayscale_array(curr_bytes)

    # 크기가 다르면 리사이즈
    if prev_arr.shape != curr_arr.shape:
        h, w = min(prev_arr.shape[0], curr_arr.shape[0]), min(prev_arr.shape[1], curr_arr.shape[1])
        prev_arr = prev_arr[:h, :w]
        curr_arr = curr_arr[:h, :w]

    score = ssim(prev_arr, curr_arr, data_range=255)
    return score < threshold
