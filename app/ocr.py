# 이미지 -> 글자 줄 (OCR). 오픈소스 RapidOCR(Apache-2.0, PaddleOCR 모델을 ONNX Runtime으로 실행)을 쓴다.
#
# - 모델: 글자 위치 찾기 PP-OCRv6 det(패키지에 포함) + 한국어 인식 korean PP-OCRv5 rec mobile
#   (한국어 모델은 처음 쓸 때 내려받아 패키지 폴더에 저장한다. 배포 시 미리 받기: python -m app.ocr)
# - 메모리를 아끼려고 엔진은 처음 쓸 때 한 번만 만들고, 동시에 한 장씩만 읽는다 (1GB 서버 기준)

import math
import threading
import time
from dataclasses import dataclass
from functools import lru_cache
from io import BytesIO
import logging

from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)

MAX_SIDE = 2400   # 긴 변이 이보다 크면 줄여서 읽는다 (휴대폰 사진은 4000px 이상이라 느리고 메모리를 많이 씀)
MIN_SIDE = 1600   # 긴 변이 이보다 작으면 키워서 읽는다 (작은 글자는 인식이 안 됨)
_lock = threading.Lock()


class ImageError(Exception):
    # 이미지가 아니거나 읽을 수 없음
    pass


@dataclass
class OcrLine:
    text: str
    score: float   # 인식 신뢰도 0~1
    x0: float      # 글자 상자 왼쪽 위 / 오른쪽 아래 (원본 이미지 픽셀 기준)
    y0: float
    x1: float
    y1: float

    @property
    def cx(self) -> float:   # 가로 가운데
        return (self.x0 + self.x1) / 2

    @property
    def cy(self) -> float:   # 세로 가운데
        return (self.y0 + self.y1) / 2

    @property
    def height(self) -> float:
        return self.y1 - self.y0


@lru_cache(maxsize=1)
def get_engine():
    from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR   # 무거운 모듈이라 처음 쓸 때 불러온다
    return RapidOCR(params={
        "Rec.lang_type": LangRec.KOREAN,
        "Rec.ocr_version": OCRVersion.PPOCRV5,
        "Rec.model_type": ModelType.MOBILE,
    })


def open_image(data: bytes) -> Image.Image:
    # 바이트 -> RGB 이미지. 휴대폰 사진의 회전(EXIF)을 바로잡고, 너무 크면 줄이고 너무 작으면 키운다.
    try:
        img = Image.open(BytesIO(data))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as e:
        raise ImageError("이미지를 읽을 수 없습니다. JPG, PNG 등 이미지 파일을 올려 주세요.") from e
    if max(img.size) > MAX_SIDE:
        img.thumbnail((MAX_SIDE, MAX_SIDE))
    elif max(img.size) < MIN_SIDE:
        scale = MIN_SIDE / max(img.size)
        img = img.resize((round(img.width * scale), round(img.height * scale)), Image.Resampling.LANCZOS)
    return img


def read_lines(data: bytes) -> list[OcrLine]:
    # 이미지 바이트 -> 글자 줄 목록 (위에서 아래, 왼쪽에서 오른쪽 순)
    img = open_image(data)
    with _lock:
        started = time.time()
        result = _run(img)
        theta = _skew(result)
        if abs(theta) >= math.radians(1):
            # 기울어진 사진: 기울어진 채로 읽으면 글자 인식이 나빠지므로(예: 53.0 -> $3.0) 이미지를 반듯하게 돌려 다시 읽는다
            img = img.rotate(math.degrees(theta), expand=True, fillcolor="white", resample=Image.Resampling.BICUBIC)
            result = _run(img)
            theta = _skew(result)
        logger.info("OCR %d줄, %.1f초", len(result.txts or ()), time.time() - started)
    if not result.txts:
        return []
    boxes = _rotate_boxes(_boxes(result), -theta)   # 남은 작은 기울기는 좌표만 돌려 맞춘다
    lines = [
        OcrLine(text=t, score=float(s), x0=min(x for x, _ in b), y0=min(y for _, y in b),
                x1=max(x for x, _ in b), y1=max(y for _, y in b))
        for b, t, s in zip(boxes, result.txts, result.scores)
    ]
    return sorted(lines, key=lambda line: (line.y0, line.x0))


def _run(img: Image.Image):
    return get_engine()(img)   # PIL 이미지를 넘긴다 (문자열을 넘기면 URL로 보고 내려받으므로 넘기지 않는다)


def _boxes(result) -> list[list[tuple[float, float]]]:
    return [[(float(x), float(y)) for x, y in b] for b in result.boxes] if result.boxes is not None else []


def _skew(result) -> float:
    # 문서의 기울기(라디안, 시계 방향 +). 짧은 상자(숫자 하나 등)는 기울기 없는 직사각형으로 나오므로
    # 긴 상자(폭 100px 초과)의 윗변 기울기 중앙값을 쓴다. (꼭짓점 순서: 왼쪽 위, 오른쪽 위, 오른쪽 아래, 왼쪽 아래)
    angles = sorted(a for b in _boxes(result) if b[1][0] - b[0][0] > 100
                    and abs(a := math.atan2(b[1][1] - b[0][1], b[1][0] - b[0][0])) < math.radians(15))
    return angles[len(angles) // 2] if angles else 0.0


def _rotate_boxes(boxes: list[list[tuple[float, float]]], angle: float) -> list[list[tuple[float, float]]]:
    if abs(angle) < math.radians(0.3):
        return boxes
    cos, sin = math.cos(angle), math.sin(angle)
    return [[(x * cos - y * sin, x * sin + y * cos) for x, y in b] for b in boxes]


if __name__ == "__main__":
    # 배포할 때 한국어 모델을 미리 내려받아 둔다 (첫 요청이 느려지거나 다운로드에 실패하지 않도록)
    get_engine()
    print("OCR 모델 준비 완료")
