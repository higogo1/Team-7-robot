"""라즈봇 메인 — 담당: 로봇 주행

모드
  idle     : 정지
  patrol   : 라인을 따라 루프를 돌며 체크포인트마다 잠깐 멈춤
  dispatch : 목표 체크포인트까지 이동 후 정지
  follow   : 등록된 사람을 따라감 (vision.py 사용)

위치 추적
  바닥 루프의 가로 테이프(체크포인트)를 지날 때마다 NODES 순서대로 한 칸씩 센다.
  시작할 때 로봇을 D와 A 사이 출발점에 두면 처음 만나는 체크포인트가 A.
  추종 모드로 라인을 벗어나면 위치를 잃는다 → 출발점에 다시 두고 '위치 초기화'.

실행: python main.py
"""
import json
import queue
import threading
import time

import websocket

import config
from camera import Camera
from hal import Hardware

try:
    from vision import FaceID
except ImportError as e:  # opencv 문제 등
    print(f"[main] vision 불러오기 실패: {e}")
    FaceID = None

LINE_WEIGHTS = [-3, -1, 1, 3]  # [왼바깥, 왼안, 오안, 오바깥] → 선이 오른쪽에 있으면 +


class Robot:
    def __init__(self):
        self.hw = Hardware()
        self.cam = Camera()
        self.face = None
        if FaceID:
            try:
                self.face = FaceID()
            except Exception as e:
                print(f"[main] 얼굴 인식 비활성: {e}")

        self.mode = "idle"
        self.idx = -1          # 마지막으로 지난 체크포인트 번호 (-1: 출발점, None: 위치 모름)
        self.target = None     # 출동 목표 노드
        self.dist = None       # 초음파 거리(cm)

        # 라인 주행 상태
        self.on_cross = False
        self.last_cross_t = 0.0
        self.pause_until = 0.0
        self.last_err = 0.0
        self.blocked = False

        # 추종 상태
        self.lost_since = None
        self.lost_alerted = False
        self.face_visible = False

        self.cmds = queue.Queue()  # 웹소켓 스레드 → 메인 루프로 명령 전달
        self.ws = None

    # ───────────── 서버 통신 ─────────────

    def connect(self):
        """관제 서버(config.SERVER_URL)에 웹소켓 연결 — 끊기면 자동 재연결"""
        def on_open(ws):
            print("[ws] 서버 연결됨")
            self.send_status()

        def on_close(ws, *args):
            print("[ws] 서버 연결 끊김, 재연결 시도...")

        self.ws = websocket.WebSocketApp(
            config.SERVER_URL, on_open=on_open, on_message=self.on_message, on_close=on_close)
        threading.Thread(
            target=lambda: self.ws.run_forever(reconnect=3, ping_interval=10, ping_timeout=5),
            daemon=True).start()

    def send(self, msg):
        try:
            if self.ws and self.ws.sock and self.ws.sock.connected:
                self.ws.send(json.dumps(msg))
        except Exception:
            pass

    def alert(self, msg, level="info"):
        print(f"[alert:{level}] {msg}")
        self.send({"type": "alert", "level": level, "msg": msg})

    @property
    def node(self):
        if self.idx is None:
            return None
        return config.NODES[self.idx] if self.idx >= 0 else "출발점"

    def send_status(self):
        self.send({
            "type": "status",
            "mode": self.mode,
            "node": self.node,
            "target": self.target,
            "dist": None if self.dist is None else round(self.dist),
            "face": self.face_visible,
        })

    def on_message(self, ws, raw):
        """서버 명령 수신 (웹소켓 스레드) → 메인 루프에서 처리"""
        try:
            self.cmds.put(json.loads(raw))
        except json.JSONDecodeError:
            pass

    # ───────────── 명령 처리 ─────────────

    def set_mode(self, mode):
        self.mode = mode
        self.pause_until = 0.0
        self.lost_since, self.lost_alerted = None, False
        self.face_visible = False
        if mode != "dispatch":
            self.target = None
        self.hw.stop()
        self.send_status()

    def handle(self, msg):
        cmd = msg.get("cmd")
        print(f"[cmd] {msg}")

        if cmd == "stop":
            self.set_mode("idle")

        elif cmd == "patrol":
            if self.idx is None:
                self.alert("위치를 모름 — 출발점에 두고 '위치 초기화'를 누르면 지점 표시가 정확해짐", "warn")
            self.set_mode("patrol")

        elif cmd == "dispatch":
            node = msg.get("node")
            if node not in config.NODES:
                return
            if self.idx is None:
                self.alert(f"{node} 출동 불가: 위치를 모름 (출발점에 두고 위치 초기화 필요)", "warn")
                return
            stopped_here = self.node == node and (self.mode == "idle" or time.time() < self.pause_until)
            if stopped_here:
                self.set_mode("idle")
                self.alert(f"이미 {node} 지점에 있음")
                return
            self.set_mode("dispatch")
            self.target = node
            self.send_status()

        elif cmd == "follow":
            if not self.face:
                self.alert("얼굴 인식 모델이 없음 (download_models.py 실행 필요)", "warn")
                return
            if not self.face.has_target:
                self.alert("따라갈 사람이 등록되지 않음 — 먼저 '특정인 등록'", "warn")
                return
            self.idx = None  # 라인을 벗어나므로 위치를 잃음
            self.set_mode("follow")

        elif cmd == "register":
            if not self.face:
                self.alert("얼굴 인식 모델이 없음 (download_models.py 실행 필요)", "warn")
                return
            frame = self.cam.read()
            if frame is not None and self.face.register(frame):
                self.alert(f"특정인 등록 완료 (얼굴 {len(self.face.targets)}장)")
            else:
                self.alert("등록 실패: 카메라에 얼굴이 보이지 않음", "warn")

        elif cmd == "reset_pos":
            self.idx = -1
            self.alert("위치 초기화: 출발점(D와 A 사이)")
            self.send_status()

    # ───────────── 라인 주행 ─────────────

    def on_checkpoint(self, now):
        if self.idx is not None:
            self.idx = (self.idx + 1) % len(config.NODES)
        node = self.node
        print(f"[line] 체크포인트 통과: {node}")

        if self.mode == "dispatch" and node == self.target:
            self.hw.stop()
            self.alert(f"{node} 지점 도착")
            self.set_mode("idle")
        elif self.mode == "patrol":
            self.pause_until = now + config.PATROL_PAUSE
            self.hw.stop()
        self.send_status()

    def line_step(self, now):
        """라인 추종 + 체크포인트(가로 테이프) 카운트"""
        if now < self.pause_until:
            self.hw.stop()
            return

        # 장애물이 가까우면 멈추고 기다림
        if self.dist is not None and self.dist < config.STOP_DISTANCE_CM:
            self.hw.stop()
            if not self.blocked:
                self.blocked = True
                self.alert(f"전방 장애물 ({self.dist:.0f}cm) — 대기 중", "warn")
            return
        self.blocked = False

        line = self.hw.read_line()
        active = sum(line)

        # 가로 테이프: 센서 3개 이상이 동시에 검정 → 체크포인트
        if active >= 3:
            if not self.on_cross and now - self.last_cross_t > config.CROSS_MIN_INTERVAL:
                self.on_cross = True
                self.last_cross_t = now
                self.on_checkpoint(now)
                if self.mode not in ("patrol", "dispatch") or now < self.pause_until:
                    return
            self.hw.drive(config.BASE_SPEED)  # 테이프 위는 직진으로 통과
            return
        self.on_cross = False

        if active == 0:
            # 선을 놓침 → 마지막으로 선이 있던 쪽으로 제자리 회전
            turn = config.SEARCH_TURN if self.last_err < 0 else -config.SEARCH_TURN
            self.hw.drive(0, 0, turn)
            return

        err = sum(w for w, on in zip(LINE_WEIGHTS, line) if on) / active
        self.last_err = err
        speed = config.BASE_SPEED * (0.6 if abs(err) >= 2 else 1.0)  # 급커브에선 감속
        self.hw.drive(speed, 0, -config.LINE_KP * err)

    # ───────────── 사람 따라가기 ─────────────

    def follow_step(self, now):
        """등록된 사람 위치에 맞춰 회전/전진"""
        frame = self.cam.read()
        if frame is None:
            self.hw.stop()
            return
        faces = self.face.detect(frame)
        target, score = self.face.find_target(frame, faces) if faces else (None, 0.0)
        self.cam.set_overlay(self.face.draw(frame, faces, target))
        self.face_visible = target is not None

        if target is None:
            self.hw.stop()
            if self.lost_since is None:
                self.lost_since = now
            elif now - self.lost_since > config.LOST_ALERT_SEC and not self.lost_alerted:
                self.lost_alerted = True
                self.alert("추종 대상을 놓침 — 대기 중", "warn")
            return
        if self.lost_alerted:
            self.alert("추종 대상을 다시 찾음")
        self.lost_since, self.lost_alerted = None, False

        x, y, w, h = target[:4]
        W = frame.shape[1]
        err_x = (x + w / 2) / W - 0.5               # 얼굴이 오른쪽이면 +
        err_size = config.FOLLOW_FACE_SIZE - w / W  # 얼굴이 작으면(멀면) +

        wz = 0 if abs(err_x) < 0.05 else -config.FOLLOW_KP_TURN * err_x
        vx = 0 if abs(err_size) < 0.02 else config.FOLLOW_KP_DIST * err_size
        vx = max(-config.FOLLOW_MAX_SPEED, min(config.FOLLOW_MAX_SPEED, vx))
        if vx > 0 and self.dist is not None and self.dist < config.STOP_DISTANCE_CM:
            vx = 0
        self.hw.drive(vx, 0, wz)

    # ───────────── 메인 루프 ─────────────

    def run(self):
        self.cam.serve(config.STREAM_PORT)
        self.connect()
        self.hw.servo(config.SERVO_PAN, config.SERVO_TILT)
        print("[main] 시작 — 대시보드에서 명령을 기다림 (Ctrl+C로 종료)")

        last_status = 0.0
        try:
            while True:
                now = time.time()
                while not self.cmds.empty():
                    self.handle(self.cmds.get())

                self.dist = self.hw.distance_cm()
                if self.mode in ("patrol", "dispatch"):
                    self.line_step(now)
                elif self.mode == "follow":
                    self.follow_step(now)  # 영상 처리 속도만큼 돌므로 sleep 없음
                else:
                    self.hw.stop()

                if now - last_status > 0.5:
                    self.send_status()
                    last_status = now
                if self.mode != "follow":
                    time.sleep(0.02)
        except KeyboardInterrupt:
            pass
        finally:
            self.hw.close()
            print("[main] 종료")


if __name__ == "__main__":
    Robot().run()
