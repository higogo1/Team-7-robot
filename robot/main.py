"""라즈봇 메인 — 경비 로봇

모드
  idle  : 정지
  guard : 경비
    roam    : 방을 계속 돌아다니며 사람 모양(다리·몸통 등)을 찾음. 제자리에서 빙글빙글 돌지 않음
              조금씩 휘어지며 앞으로 → 몇 초마다 방향을 무작위로 바꿈, 카메라는 좌우로 천천히 훑음
              앞이 막히면 크게 틀고, 끼이면 잠깐 후진
    confirm : 사람이 보이면 즉시 멈추고, 카메라를 그쪽으로 돌린 뒤 위로 올리며 얼굴을 찾음
    follow  : 카메라 좌우는 정면. 사람을 화면 가운데에 두며 따라감
              부딪히지 않게: 초음파(FOLLOW_KEEP_CM)와 카메라 속 사람 크기(FOLLOW_STOP_H) 둘 다로 전진을 멈추고,
              초음파로 FOLLOW_BACK_CM 보다 가까울 때만 뒤로 물러남
              얼굴이 보이는 동안 FACE_PHOTO_SEC 마다 사진 촬영
              GUARD_LOST_SEC 넘게 안 보이면 다시 roam

알림: 상태가 바뀔 때만 보냄 (같은 내용은 ALERT_REPEAT_SEC 안에 다시 보내지 않음)
안전: 서버 연결이 FAILSAFE_SEC 넘게 끊기면 스스로 정지 / health.py 가 기기 이상(통제불능)을 감시

실행: python main.py
"""
import json
import math
import queue
import random
import threading
import time
import urllib.request

import cv2
import websocket

import config
from camera import Camera
from hal import Hardware
from health import HealthMonitor

try:
    from vision import FaceDetector, PersonFinder
except ImportError as e:  # opencv 문제 등
    print(f"[main] vision 불러오기 실패: {e}")
    FaceDetector = PersonFinder = None


class Robot:
    def __init__(self):
        self.hw = Hardware()
        self.cam = Camera()
        self.face = self._load(FaceDetector, "얼굴 인식")
        self.people = self._load(PersonFinder, "사람 인식", config.PERSON_THRESHOLD)

        self.mode = "idle"
        self.nav = None              # 경비 단계: roam / confirm / follow
        self.dist = None             # 초음파 거리(cm)
        self.pan = float(config.SERVO_PAN)    # 카메라 좌우 각도 (90: 정면, 크면 왼쪽)
        self.tilt = float(config.SERVO_TILT)  # 카메라 상하 각도 (크면 위)
        self.servo_t = 0.0           # 카메라 서보를 마지막으로 움직인 시각 (기기 이상 감시용)
        self.cam_move_t = 0.0        # '돌리고 → 기다렸다 → 보기' 용 마지막 카메라 이동 시각
        self.blocked = False         # 전방 장애물 경고를 보냈는지

        # 경비 상태
        self.roam = None             # 수색 단계: ("move", 끝, 휘어짐) / ("arc"|"turn", 끝, 방향) / ("back", 끝, 0)
        self.roam_alerted = False    # '돌아다니며 수색' 알림을 보냈는지 (사람을 찾기 전까지 한 번만)
        self.steer_side = 1          # 벽에 가까워질 때 비켜 갈 방향
        self.turn_start = 0.0
        self.person_seen_t = 0.0
        self.person_cx = None        # 따라가는 사람의 마지막 가로 위치
        self.face_visible = False
        self.face_found = False      # 이번 추적에서 얼굴을 한 번이라도 찾았는지 (알림 한 번만)
        self.confirm_since = 0.0
        self.last_face_photo = 0.0
        self.drive_cmd = (0.0, 0.0)  # 따라갈 때 마지막 (전진, 회전) — 잠깐 놓쳤을 때 유지용

        self.recent_alerts = {}      # 알림 내용 → 마지막으로 보낸 시각 (반복 방지)
        self.cmds = queue.Queue()    # 웹소켓 스레드 → 메인 루프로 명령 전달
        self.ws = None
        self.ws_down_since = time.time()  # 서버 연결이 끊긴 시각 (연결 중이면 None)
        self.failsafe = None              # 통신 끊김으로 안전 정지했을 때 (이전 모드, 시각)
        self.health = HealthMonitor(self)

    @staticmethod
    def _load(cls, name, *args):
        if cls is None:
            return None
        try:
            return cls(*args)
        except Exception as e:
            print(f"[main] {name} 비활성: {e}")
            return None

    # ───────────── 서버 통신 ─────────────

    def connect(self):
        """관제 서버(config.SERVER_URL)에 웹소켓 연결 — 끊기면 자동 재연결"""
        def on_open(ws):
            print("[ws] 서버 연결됨")
            down = time.time() - self.ws_down_since if self.ws_down_since else 0
            self.ws_down_since = None
            self.send_status()
            if self.failsafe:
                mode, _ = self.failsafe
                self.failsafe = None
                self.alert(f"서버 연결이 {down:.0f}초 끊겨 안전 정지했었음 — 상황 확인 후 다시 시작", "critical")

        def on_close(ws, *args):
            print("[ws] 서버 연결 끊김, 재연결 시도...")
            if self.ws_down_since is None:
                self.ws_down_since = time.time()

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
        """대시보드 알림. 같은 내용은 ALERT_REPEAT_SEC 안에 다시 보내지 않음"""
        now = time.time()
        if now - self.recent_alerts.get(msg, -1e9) < config.ALERT_REPEAT_SEC:
            return
        self.recent_alerts[msg] = now
        print(f"[alert:{level}] {msg}")
        self.send({"type": "alert", "level": level, "msg": msg})

    def send_status(self):
        self.send({
            "type": "status",
            "mode": self.mode,
            "nav": self.nav,
            "person": self.mode == "guard" and self.nav in ("confirm", "follow"),  # 사람을 확인·추적 중 (경보)
            "face": self.face_visible,
            "dist": None if self.dist is None else round(self.dist),
            "move": self.move_state(),
            "pan": round(self.pan), "tilt": round(self.tilt),
            "health": self.health.issues() if hasattr(self, "health") else [],
        })

    def move_state(self):
        vx, _, wz = getattr(self.hw, "last_cmd", (0, 0, 0))
        if abs(vx) >= 1:
            return "forward" if vx > 0 else "back"
        if abs(wz) >= 1:
            return "left" if wz > 0 else "right"
        return "stop"

    def on_message(self, ws, raw):
        """서버 명령 수신 (웹소켓 스레드) → 메인 루프에서 처리"""
        try:
            self.cmds.put(json.loads(raw))
        except json.JSONDecodeError:
            pass

    # ───────────── 명령 처리 ─────────────

    def set_mode(self, mode):
        self.mode = mode
        self.nav = None
        self.blocked = False
        self.face_visible = self.face_found = False
        self.person_cx = None
        self.roam, self.roam_alerted = None, False
        self.center_camera()
        self.hw.stop()
        self.send_status()

    def handle(self, msg):
        cmd = msg.get("cmd")
        print(f"[cmd] {msg}")
        if cmd == "stop":
            self.set_mode("idle")
        elif cmd in ("guard", "patrol", "follow"):     # patrol/follow 는 예전 이름 (같은 경비 모드)
            if not self.people:
                self.alert("사람 인식 모델이 없어 경비를 시작할 수 없음 (download_models.py 실행)", "warn")
                return
            if self.mode != "guard":
                self.set_mode("guard")
            src = msg.get("source")
            self.alert(f"센서 {src} 이벤트 — 경비 시작" if src else "경비 시작", "warn" if src else "info")

    # ───────────── 카메라 / 장애물 ─────────────

    def set_pan(self, angle):
        angle = max(0.0, min(180.0, angle))
        if abs(angle - self.pan) >= 1:  # 1도 미만 변화는 무시 (I2C 부하 줄이기)
            self.pan, self.servo_t = angle, time.time()
            self.hw.pan(angle)

    def set_tilt(self, angle):
        angle = max(0.0, min(110.0, angle))
        if abs(angle - self.tilt) >= 1:
            self.tilt, self.servo_t = angle, time.time()
            self.hw.tilt(angle)

    def center_camera(self):
        self.set_pan(config.SERVO_PAN)
        self.set_tilt(config.SERVO_TILT)

    def check_blocked(self):
        """앞이 막혔는지. 막히면 경고 1번. STOP_DISTANCE_CM+10 보다 멀어져야 풀림 (경고 반복 방지)"""
        d = self.dist
        if d is not None and d < config.STOP_DISTANCE_CM:
            if not self.blocked:
                self.blocked = True
                self.alert(f"전방 장애물 ({d:.0f}cm) — 앞으로 가지 않고 대기", "warn")
        elif d is None or d > config.STOP_DISTANCE_CM + 10:
            self.blocked = False
        return self.blocked and d is not None and d < config.STOP_DISTANCE_CM + 10

    # ───────────── 사진 ─────────────

    def upload_photo(self, frame, reason):
        """사진을 관제 서버로 보냄 (주행이 멈추지 않게 별도 스레드)"""
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
        if not ok:
            return
        base = config.SERVER_URL.replace("ws://", "http://").replace("wss://", "https://").split("/ws/")[0]

        def post():
            try:
                req = urllib.request.Request(f"{base}/api/photo?reason={reason}", data=buf.tobytes(),
                                             method="POST", headers={"Content-Type": "image/jpeg"})
                urllib.request.urlopen(req, timeout=5).read()
            except Exception as e:
                print(f"[photo] 사진 전송 실패: {e}")

        threading.Thread(target=post, daemon=True).start()

    def face_photo(self, frame, now):
        """얼굴이 보이면 FACE_PHOTO_SEC 마다 촬영. 처음 찾았을 때만 알림"""
        if now - self.last_face_photo < config.FACE_PHOTO_SEC:
            return
        self.last_face_photo = now
        self.upload_photo(frame, "face")
        if not self.face_found:
            self.face_found = True
            self.alert(f"얼굴 확인 — 촬영 시작 ({config.FACE_PHOTO_SEC:.0f}초마다)", "warn")

    # ───────────── 경비 ─────────────

    def begin_search(self, now):
        """수색 시작: 방을 돌아다니며 찾음 (제자리에서 빙글빙글 돌지 않음)"""
        self.nav = "roam"
        self.person_cx = None
        self.face_visible = self.face_found = False
        self.set_tilt(config.SERVO_TILT)
        self.new_leg(now)
        self.send_status()

    def new_leg(self, now):
        """앞으로 가는 한 구간: 무작위 길이, 무작위로 살짝 휘어짐. 벽을 비켜 갈 방향도 미리 정함"""
        curve = random.uniform(-config.ROAM_CURVE, config.ROAM_CURVE)
        self.roam = ("move", now + random.uniform(*config.ROAM_LEG_SEC), curve)
        self.steer_side = 1 if curve >= 0 else -1

    def turn_away(self, now, secs, why, arc=False):
        """무작위 방향으로 틀기 (secs: 시간 범위). arc=True 면 앞으로 가면서 휘어 돎, False 면 제자리에서"""
        side = random.choice((-1, 1))
        self.roam = ("arc" if arc else "turn", now + random.uniform(*secs), side)
        self.turn_start = now
        print(f"[roam] {why} → {'왼' if side > 0 else '오른'}쪽으로 틂")

    def search(self, now):
        """아무도 안 보일 때: 방을 계속 돌아다니며 찾음. 카메라는 좌우로 천천히 훑어 옆도 봄"""
        if not self.roam_alerted:
            self.roam_alerted = True
            self.alert("주변에 사람이 없어 돌아다니며 수색")
        if self.roam is None:
            self.new_leg(now)
        # 카메라 좌우 훑기 (사람이 보이면 확인 단계에서 다시 맞춤)
        sweep = config.ROAM_PAN_SWEEP * math.sin(2 * math.pi * now / config.ROAM_PAN_PERIOD)
        self.set_pan(config.SERVO_PAN + sweep)

        stage, until, arg = self.roam
        d = self.dist
        if stage == "move":
            if "stuck" in self.health.active:              # 끼임·들림 → 잠깐 후진 후 방향 바꿈
                self.roam = ("back", now + config.ROAM_BACK_SEC, 0)
                print("[roam] 끼임 의심 → 후진")
                return
            if d is not None and d < config.ROAM_AVOID_CM:  # 앞이 막힘 → 제자리에서 틀기 (트이면 바로 출발)
                self.hw.stop()
                self.turn_away(now, config.ROAM_AVOID_TURN_SEC, f"앞 {d:.0f}cm 막힘")
                return
            if now >= until:                                 # 한 구간 끝 → 앞으로 가며 휘어서 방향 바꾸기
                self.turn_away(now, config.ROAM_TURN_SEC, "방향 바꾸기", arc=True)
                return
            if d is not None and d < config.ROAM_AVOID_CM * 2:
                # 벽에 가까워지는 중 → 천천히 가며 미리 휘어서 비켜 감 (멈춰서 도는 일을 줄임)
                self.hw.drive(config.MIN_SPEED, 0, self.steer_side * config.SCAN_STEP_SPEED)
                return
            self.hw.drive(config.ROAM_SPEED, 0, arg)
        elif stage == "back":
            if now >= until:
                self.turn_away(now, config.ROAM_AVOID_TURN_SEC, "후진 끝")
                return
            self.hw.drive(-config.ROAM_SPEED, 0, 0)
        else:  # turn (제자리) / arc (앞으로 가며 휘어 돎)
            cleared = (stage == "turn" and now - self.turn_start > 0.25
                       and (d is None or d > config.ROAM_CLEAR_CM))   # 앞이 트였으면 바로 출발
            if now >= until or cleared:
                self.new_leg(now)
                return
            if stage == "arc" and not (d is not None and d < config.ROAM_AVOID_CM):
                self.hw.drive(config.MIN_SPEED, 0, arg * config.SCAN_STEP_SPEED)
            else:
                self.hw.drive(0, 0, arg * config.SCAN_STEP_SPEED)

    def pick_person(self, people):
        """따라갈 사람: 이전 위치와 가장 가까운 사람 (처음엔 가장 큰 사람)"""
        people = [p for p in people if p["h"] >= config.PERSON_MIN_H]
        if not people:
            return None
        if self.person_cx is None:
            return people[0]
        return min(people, key=lambda p: abs(p["cx"] - self.person_cx))

    def find_face(self, frame, person):
        """사람 박스 근처(가로)에서 가장 큰 얼굴 → (face, 가로위치, 세로위치) 또는 None"""
        if not self.face:
            return None
        H, W = frame.shape[:2]
        best = None
        for f in self.face.detect(frame):
            x, y, w, h = f[:4]
            fx, fy = (x + w / 2) / W, (y + h / 2) / H
            if person is not None and abs(fx - person["cx"]) > 0.3:
                continue
            if best is None or w * h > best[0][2] * best[0][3]:
                best = (f, fx, fy)
        return best

    def set_nav(self, nav):
        if self.nav != nav:
            self.nav = nav
            self.send_status()

    def guard_step(self, now):
        frame = self.cam.read()
        if frame is None:
            self.hw.stop()
            return
        people = self.people.detect(frame)
        p = self.pick_person(people)
        f = self.find_face(frame, p) if p is not None and self.nav in ("confirm", "follow") else None
        self.cam.set_overlay(self.draw(frame, people, p, f))
        if p is not None:
            self.person_seen_t, self.person_cx = now, p["cx"]
        if (f is not None) != self.face_visible:
            self.face_visible = f is not None
            self.send_status()

        if self.nav is None:
            self.begin_search(now)

        # 경계·수색: 사람 모양이 보이면 (이동 중이라도) 즉시 멈추고 확인 시작
        if self.nav == "roam":
            if p is not None:
                self.hw.stop()
                self.roam, self.roam_alerted = None, False
                self.confirm_since, self.cam_move_t = now, 0.0
                print(f"[guard] 사람 감지 (가로 {p['cx']:.2f}, 높이 {p['h']:.2f}) → 확인")
                self.set_nav("confirm")
                return
            self.person_cx = None
            self.search(now)
            return

        # 사람이 안 보임: 잠깐은 하던 동작 유지, 오래 안 보이면 다시 경계
        if p is None:
            lost = now - self.person_seen_t
            if self.nav == "follow" and lost < config.FOLLOW_COAST:
                vx, wz = self.drive_cmd
                self.hw.drive(0 if self.check_blocked() else vx, 0, wz)
                return
            self.hw.stop()
            if lost > config.GUARD_LOST_SEC:
                self.alert("대상을 놓침 — 다시 수색")
                self.begin_search(now)
            return

        # 확인: 카메라를 사람 쪽으로 돌리고 위로 올리며 얼굴 찾기 ('돌리고 → 기다렸다 → 보기')
        if self.nav == "confirm":
            self.hw.stop()
            if f is not None:
                self.face_photo(frame, now)
                self.start_follow(now)
                return
            if now - self.cam_move_t >= config.CAM_SETTLE:
                ex = p["cx"] - 0.5
                if abs(ex) > config.CENTER_TOL:
                    step = max(-config.PAN_MAX_STEP, min(config.PAN_MAX_STEP, config.PAN_TRACK_K * ex))
                    self.set_pan(self.pan - step)   # 팬이 클수록 왼쪽 → 사람이 오른쪽이면 팬을 줄임
                if self.tilt < config.FACE_TILT_MAX:
                    self.set_tilt(self.tilt + config.FACE_TILT_STEP)
                self.cam_move_t = now
            if now - self.confirm_since > config.CONFIRM_TIMEOUT:
                self.alert("얼굴을 확인하지 못함 — 사람 모양을 따라가며 계속 확인")
                self.start_follow(now)
            return

        # 추적: 사람을 화면 가운데 두며 따라감, 초음파로 거리 유지, 얼굴이 보이면 10초마다 촬영
        if f is not None:
            self.face_photo(frame, now)
            if now - self.cam_move_t >= config.CAM_SETTLE and abs(f[2] - 0.4) > 0.15:
                self.set_tilt(self.tilt - config.TILT_TRACK_K * (f[2] - 0.4))   # 얼굴이 화면 위쪽에 오게
                self.cam_move_t = now
        elif (p["top"] < 0.05 and self.tilt < config.FACE_TILT_MAX
              and now - self.cam_move_t >= 2 * config.CAM_SETTLE):
            # 가까워져서 머리가 화면 위로 잘림 → 카메라를 조금 올려 얼굴을 다시 찾음
            self.set_tilt(self.tilt + config.FACE_TILT_STEP / 2)
            self.cam_move_t = now
        err = p["cx"] - 0.5
        wz = 0 if abs(err) < 0.05 else -config.FOLLOW_KP_TURN * err
        d, h = self.dist, p["h"]
        # 후진은 초음파로 실제 거리가 가까울 때만. 카메라가 낮아 1m 넘게 떨어져도 사람이 화면을 꽉 채우므로
        # 화면 속 크기로는 후진하지 않고 '더 다가가지 않기'(정지)에만 씀
        too_close = d is not None and d < config.FOLLOW_BACK_CM
        close = (d is not None and d < config.FOLLOW_KEEP_CM) or h >= config.FOLLOW_STOP_H
        if too_close:
            vx, wz = -config.MIN_SPEED, 0.0     # 너무 가까움 → 뒤로 물러남 (부딪히지 않게)
        elif close or abs(err) > config.FOLLOW_ALIGN_FIRST:
            vx = 0.0                            # 충분히 가깝거나 방향이 많이 틀어짐 → 전진 멈춤
        else:
            near = h >= config.FOLLOW_SLOW_H
            vx = config.MIN_SPEED if near else max(config.MIN_SPEED, config.FOLLOW_SPEED)
        if vx > 0 and self.check_blocked():
            vx = 0.0
        if vx == 0 and wz:
            wz = max(config.MIN_TURN, abs(wz)) * (1 if wz > 0 else -1)
        self.drive_cmd = (vx, wz)
        self.hw.drive(vx, 0, wz)

    def start_follow(self, now):
        """확인 끝 → 카메라 좌우는 정면으로(몸으로 방향을 맞춤), 높이는 얼굴 쪽 유지"""
        self.set_pan(config.SERVO_PAN)
        self.cam_move_t = now
        self.drive_cmd = (0.0, 0.0)
        print(f"[guard] 추적 시작 (얼굴 {'있음' if self.face_found else '없음'}, 틸트 {self.tilt:.0f})")
        self.set_nav("follow")

    def draw(self, frame, people, target, face):
        """스트림 표시용: 대상 사람 박스(추적 중 빨강) + 얼굴 박스"""
        out = frame.copy()
        for q in people:
            x1, y1, x2, y2 = q["box"]
            is_t = q is target
            color = (0, 0, 255) if is_t and self.nav == "follow" else (0, 220, 0) if is_t else (0, 200, 255)
            cv2.rectangle(out, (x1, y1), (x2, y2), color, 3 if is_t else 1)
            if is_t:
                label = {"roam": "PERSON", "confirm": "CHECKING", "follow": "TRACKING"}.get(self.nav, "PERSON")
                cv2.putText(out, label, (x1, max(25, y1 - 10)), cv2.FONT_HERSHEY_SIMPLEX, 0.9, color, 2)
        if face is not None:
            x, y, w, h = map(int, face[0][:4])
            cv2.rectangle(out, (x, y), (x + w, y + h), (255, 255, 255), 2)
            cv2.putText(out, "FACE", (x, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
        return out

    # ───────────── 안전 ─────────────

    def check_failsafe(self, now):
        """안전 정지: 서버 연결이 끊긴 채 움직이고 있으면 멈춤 (원격으로 멈출 수 없는 상태 방지)"""
        if self.ws_down_since and now - self.ws_down_since > config.FAILSAFE_SEC and self.mode != "idle":
            print(f"[failsafe] 서버 연결 {now - self.ws_down_since:.0f}초 끊김 → 안전 정지")
            self.failsafe = (self.mode, now)
            self.set_mode("idle")

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
                self.health.check(now)
                self.check_failsafe(now)

                if self.mode == "guard":
                    self.guard_step(now)   # 영상 처리 시간이 곧 대기 시간
                else:
                    self.hw.stop()
                    time.sleep(0.02)

                if now - last_status > 0.5:
                    self.send_status()
                    last_status = now
        except KeyboardInterrupt:
            pass
        finally:
            self.hw.close()
            print("[main] 종료")


if __name__ == "__main__":
    Robot().run()
