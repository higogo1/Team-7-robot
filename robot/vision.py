"""사람·얼굴 인식 — 경비 로봇용 (모델 파일 없이는 동작하지 않음 → python download_models.py)

PersonFinder : 사람 모양 인식 (다리·몸통 등 몸 일부만 보여도) — NanoDet-Plus, COCO 0번 클래스
FaceDetector : 얼굴 찾기 — OpenCV 내장 YuNet
"""
from pathlib import Path

import cv2
import numpy as np

HERE = Path(__file__).parent


class FaceDetector:
    """얼굴 찾기 (YuNet). 검출은 축소 영상에서 해서 라즈베리파이에서도 빠름"""

    MODEL = HERE / "models" / "face_detection_yunet_2023mar.onnx"
    DETECT_W = 320

    def __init__(self):
        if not self.MODEL.exists():
            raise FileNotFoundError("얼굴 인식 모델 없음 → robot/ 폴더에서 python download_models.py 실행")
        self.detector = cv2.FaceDetectorYN.create(str(self.MODEL), "", (self.DETECT_W, self.DETECT_W), 0.8, 0.3, 50)

    def detect(self, frame):
        """얼굴 목록. 각 얼굴은 원본 좌표계의 YuNet 결과 행 (x, y, w, h, 랜드마크..., score)"""
        h, w = frame.shape[:2]
        scale = self.DETECT_W / w
        small = cv2.resize(frame, (self.DETECT_W, int(h * scale)))
        self.detector.setInputSize((small.shape[1], small.shape[0]))
        _, faces = self.detector.detect(small)
        if faces is None:
            return []
        faces = faces.copy()
        faces[:, :14] /= scale  # 박스 + 랜드마크 5개 좌표를 원본 크기로
        return list(faces)


class PersonFinder:
    """사람 인식 (몸 일부만 보여도) — NanoDet-Plus (COCO, 0번 클래스 = 사람)
    후처리는 opencv_zoo models/object_detection_nanodet (Apache 2.0) 을 참고해 정리함."""

    MODEL = HERE / "models" / "object_detection_nanodet_2022nov.onnx"
    SIZE = 416
    STRIDES = (8, 16, 32, 64)
    REG_MAX = 7

    def __init__(self, threshold=0.35):
        if not self.MODEL.exists():
            raise FileNotFoundError("사람 인식 모델 없음 → robot/ 폴더에서 python download_models.py 실행")
        self.net = cv2.dnn.readNet(str(self.MODEL))
        self.threshold = threshold
        self.mean = np.array([103.53, 116.28, 123.675], np.float32).reshape(1, 1, 3)
        self.std = np.array([57.375, 57.12, 58.395], np.float32).reshape(1, 1, 3)
        self.project = np.arange(self.REG_MAX + 1)
        self.anchors = []
        for s in self.STRIDES:
            n = self.SIZE // s
            xv, yv = np.meshgrid(np.arange(n) * s, np.arange(n) * s)
            self.anchors.append(np.column_stack((xv.ravel() + 0.5 * (s - 1), yv.ravel() + 0.5 * (s - 1))))

    def detect(self, frame):
        """사람 목록 → [{"box": (x1,y1,x2,y2) px, "score", "cx","top","bottom": 0~1, "h": 높이/화면높이}] (큰 것부터)"""
        H, W = frame.shape[:2]
        scale = self.SIZE / max(H, W)
        nw, nh = int(W * scale), int(H * scale)
        top, left = (self.SIZE - nh) // 2, (self.SIZE - nw) // 2
        img = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB), (nw, nh), interpolation=cv2.INTER_AREA)
        img = cv2.copyMakeBorder(img, top, self.SIZE - nh - top, left, self.SIZE - nw - left,
                                 cv2.BORDER_CONSTANT, value=0)
        blob = cv2.dnn.blobFromImage((img.astype(np.float32) - self.mean) / self.std)
        self.net.setInput(blob)
        outs = self.net.forward(self.net.getUnconnectedOutLayersNames())

        # 출력 순서는 OpenCV 버전마다 다를 수 있어 모양으로 구분: 분류(80칸) / 위치(32칸), 큰 것(작은 stride)부터
        outs = [o.reshape(-1, o.shape[-1]) for o in outs]
        cls_list = sorted([o for o in outs if o.shape[1] == 80], key=len, reverse=True)
        reg_list = sorted([o for o in outs if o.shape[1] == 4 * (self.REG_MAX + 1)], key=len, reverse=True)
        boxes, scores = [], []
        for stride, cls, reg, anchors in zip(self.STRIDES, cls_list, reg_list, self.anchors):
            person = cls[:, 0]                       # 0번 = 사람
            keep = person > self.threshold * 0.8
            if not keep.any():
                continue
            e = np.exp(reg[keep].reshape(-1, self.REG_MAX + 1))
            dist = (e / e.sum(axis=1, keepdims=True)) @ self.project
            dist = dist.reshape(-1, 4) * stride
            a = anchors[keep]
            b = np.column_stack([a[:, 0] - dist[:, 0], a[:, 1] - dist[:, 1],
                                 a[:, 0] + dist[:, 2], a[:, 1] + dist[:, 3]])
            boxes.append(b)
            scores.append(person[keep])
        if not boxes:
            return []
        boxes, scores = np.concatenate(boxes), np.concatenate(scores)
        wh = boxes.copy()
        wh[:, 2:] -= wh[:, :2]
        idx = cv2.dnn.NMSBoxes(wh.tolist(), scores.tolist(), self.threshold, 0.5)
        people = []
        for i in np.array(idx).ravel():
            x1, y1, x2, y2 = boxes[i]
            x1, x2 = np.clip([(x1 - left) / scale, (x2 - left) / scale], 0, W)
            y1, y2 = np.clip([(y1 - top) / scale, (y2 - top) / scale], 0, H)
            if x2 - x1 < 8 or y2 - y1 < 8:
                continue
            people.append({"box": (int(x1), int(y1), int(x2), int(y2)), "score": float(scores[i]),
                           "cx": (x1 + x2) / 2 / W, "top": y1 / H, "bottom": y2 / H, "h": (y2 - y1) / H})
        people.sort(key=lambda p: (p["box"][2] - p["box"][0]) * (p["box"][3] - p["box"][1]), reverse=True)
        return people
