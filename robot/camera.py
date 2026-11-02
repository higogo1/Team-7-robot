"""카메라 캡처 + MJPEG 스트리밍 — 담당: 로봇 비전

웹 대시보드에서 http://로봇IP:8080/stream 으로 영상을 볼 수 있게 한다.
  /stream        MJPEG 실시간 영상
  /snapshot.jpg  현재 프레임 한 장
"""
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import cv2

import config


class Camera:
    def __init__(self):
        self.cap = cv2.VideoCapture(config.CAMERA_INDEX)
        # MJPG로 고정: 지정 안 하면 무압축(YUYV)으로 열려 1280x720에서 10fps로 떨어짐
        self.cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        self.cap.set(cv2.CAP_PROP_FRAME_WIDTH, config.FRAME_W)
        self.cap.set(cv2.CAP_PROP_FRAME_HEIGHT, config.FRAME_H)
        self.cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if not self.cap.isOpened():
            print("[camera] 카메라를 열 수 없음 — CAMERA_INDEX 확인 (ls /dev/video*)")
        self._frame = None
        self._frame_t = time.time()  # 마지막으로 새 화면이 들어온 시각 (기기 이상 감시용)
        self._overlay = None      # 비전 결과를 그린 프레임 (있으면 스트림에 이걸 보냄)
        self._overlay_t = 0.0
        self._jpeg = None
        self._lock = threading.Lock()
        threading.Thread(target=self._capture_loop, daemon=True).start()

    def _capture_loop(self):
        while True:
            ok, frame = self.cap.read()
            if not ok:
                time.sleep(0.1)
                continue
            with self._lock:
                self._frame = frame
                self._frame_t = time.time()

    def frame_age(self):
        """마지막 새 화면 이후 지난 시간(초)"""
        return time.time() - self._frame_t

    def read(self):
        """최신 프레임 반환 (없으면 None)"""
        with self._lock:
            return None if self._frame is None else self._frame.copy()

    def set_overlay(self, frame):
        """얼굴 박스 등을 그린 프레임을 스트림에 표시"""
        with self._lock:
            self._overlay = frame
            self._overlay_t = time.time()

    def _stream_frame(self, max_w=None):
        with self._lock:
            use_overlay = self._overlay is not None and time.time() - self._overlay_t < 0.5
            frame = self._overlay if use_overlay else self._frame
        if frame is None:
            return None
        if max_w and frame.shape[1] > max_w:  # 웹 스트림은 작게 (와이파이 부담 줄이기)
            frame = cv2.resize(frame, (max_w, frame.shape[0] * max_w // frame.shape[1]))
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        return buf.tobytes() if ok else None

    def serve(self, port):
        """MJPEG 스트리밍 서버 시작 (백그라운드 스레드)"""
        cam = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                if self.path.startswith("/snapshot"):
                    jpg = cam._stream_frame()
                    if jpg is None:
                        self.send_error(503)
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(jpg)))
                    self.send_header("Access-Control-Allow-Origin", "*")
                    self.end_headers()
                    self.wfile.write(jpg)
                    return
                if not self.path.startswith("/stream"):
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                try:
                    while True:
                        jpg = cam._stream_frame(max_w=config.STREAM_W)
                        if jpg is not None:
                            self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n"
                                             + f"Content-Length: {len(jpg)}\r\n\r\n".encode()
                                             + jpg + b"\r\n")
                        time.sleep(1 / 15)  # 15fps면 충분, CPU 아끼기
                except (BrokenPipeError, ConnectionResetError):
                    pass

        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        print(f"[camera] 스트리밍: http://<로봇IP>:{port}/stream")
