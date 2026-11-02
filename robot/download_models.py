"""사람·얼굴 인식 모델(OpenCV Zoo) 내려받기 — robot/ 폴더에서 한 번만 실행
   python download_models.py
"""
import urllib.request
from pathlib import Path

MODELS = {
    # 얼굴 찾기 (YuNet)
    "face_detection_yunet_2023mar.onnx":
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    # 사람(몸 일부만 보여도) 인식용 NanoDet-Plus (COCO 80종, 0번 = 사람)
    "object_detection_nanodet_2022nov.onnx":
        "https://github.com/opencv/opencv_zoo/raw/main/models/object_detection_nanodet/object_detection_nanodet_2022nov.onnx",
}

out = Path(__file__).parent / "models"
out.mkdir(exist_ok=True)
for name, url in MODELS.items():
    path = out / name
    if path.exists() and path.stat().st_size > 100_000:
        print(f"이미 있음: {name}")
        continue
    print(f"다운로드: {name} ...")
    urllib.request.urlretrieve(url, path)
    print(f"  완료 ({path.stat().st_size // 1024} KB)")
