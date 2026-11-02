"""얼굴 검출/인식 모델(OpenCV Zoo) 내려받기 — robot/ 폴더에서 한 번만 실행
   python download_models.py
"""
import urllib.request
from pathlib import Path

MODELS = {
    "face_detection_yunet_2023mar.onnx":
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx":
        "https://github.com/opencv/opencv_zoo/raw/main/models/face_recognition_sface/face_recognition_sface_2021dec.onnx",
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
