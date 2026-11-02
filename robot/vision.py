"""특정인 얼굴 등록/인식 — 담당: 로봇 비전

OpenCV 내장 YuNet(얼굴 검출) + SFace(얼굴 인식) 사용.
모델 파일은 robot/models/ 에 둔다 → `python download_models.py` 로 받기.
등록한 얼굴 특징값은 robot/target.npy 에 저장되어 재시작해도 유지된다.
"""
from pathlib import Path

import cv2
import numpy as np

import config

HERE = Path(__file__).parent
DET_MODEL = HERE / "models" / "face_detection_yunet_2023mar.onnx"
REC_MODEL = HERE / "models" / "face_recognition_sface_2021dec.onnx"
TARGET_FILE = HERE / "target.npy"
DETECT_W = 320  # 검출은 축소 영상에서 (라즈베리파이 속도 확보)


class FaceID:
    def __init__(self):
        if not DET_MODEL.exists() or not REC_MODEL.exists():
            raise FileNotFoundError("얼굴 모델 없음 → robot/ 폴더에서 python download_models.py 실행")
        self.detector = cv2.FaceDetectorYN.create(str(DET_MODEL), "", (DETECT_W, DETECT_W), 0.8, 0.3, 50)
        self.recognizer = cv2.FaceRecognizerSF.create(str(REC_MODEL), "")
        self.targets = []  # 등록된 얼굴 특징값 목록 (여러 장 등록하면 인식이 더 안정적)
        if TARGET_FILE.exists():
            self.targets = list(np.load(TARGET_FILE))
            print(f"[vision] 등록된 얼굴 {len(self.targets)}개 불러옴")

    @property
    def has_target(self):
        return len(self.targets) > 0

    def detect(self, frame):
        """프레임에서 얼굴 목록 검출. 각 얼굴은 원본 좌표계의 YuNet 결과 행 (x, y, w, h, 랜드마크..., score)"""
        h, w = frame.shape[:2]
        scale = DETECT_W / w
        small = cv2.resize(frame, (DETECT_W, int(h * scale)))
        self.detector.setInputSize((small.shape[1], small.shape[0]))
        _, faces = self.detector.detect(small)
        if faces is None:
            return []
        faces = faces.copy()
        faces[:, :14] /= scale  # 박스 + 랜드마크 5개 좌표를 원본 크기로
        return list(faces)

    def _feature(self, frame, face):
        aligned = self.recognizer.alignCrop(frame, face)
        return self.recognizer.feature(aligned)

    def register(self, frame, append=True):
        """화면에서 가장 큰 얼굴을 따라갈 사람으로 저장. 성공하면 True"""
        faces = self.detect(frame)
        if not faces:
            return False
        face = max(faces, key=lambda f: f[2] * f[3])
        feat = self._feature(frame, face)
        self.targets = (self.targets if append else []) + [feat]
        self.targets = self.targets[-5:]  # 최근 5장까지
        np.save(TARGET_FILE, np.array(self.targets))
        return True

    def clear(self):
        self.targets = []
        TARGET_FILE.unlink(missing_ok=True)

    def find_target(self, frame, faces):
        """등록된 사람 얼굴 찾기 → (face, score) 또는 (None, 최고점수)"""
        best, best_score = None, 0.0
        for face in faces:
            feat = self._feature(frame, face)
            score = max(self.recognizer.match(feat, t, cv2.FaceRecognizerSF_FR_COSINE) for t in self.targets)
            if score > best_score:
                best, best_score = face, score
        if best is not None and best_score >= config.MATCH_THRESHOLD:
            return best, best_score
        return None, best_score

    @staticmethod
    def draw(frame, faces, target):
        """스트림 표시용: 대상은 초록, 나머지는 회색 박스"""
        out = frame.copy()
        for f in faces:
            x, y, w, h = map(int, f[:4])
            is_target = target is not None and np.array_equal(f, target)
            color = (0, 220, 0) if is_target else (160, 160, 160)
            cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
            if is_target:
                cv2.putText(out, "TARGET", (x, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)
        return out
