"""기기 이상(통제불능) 감시 — 로봇이 공격·외부 요인으로 제대로 움직이지 않을 때 알림

이 로봇에는 기울기/가속도 센서가 없어서, 이미 있는 카메라·초음파·전원·통신 정보로 판단한다.

  camera_stale   : 새 카메라 화면이 안 들어옴          → 카메라 고장·분리
  camera_blocked : 화면이 거의 새까맘                  → 카메라 가림
  stuck          : 바퀴를 돌리는데 화면이 그대로        → 끼임·들림·바퀴 고장
  moved          : 멈춤 상태인데 화면 전체가 계속 밀림  → 들어 올려 옮겨지거나 밀림
  sensor         : 로봇 보드와 I2C 통신 오류가 계속됨    → 센서·모터 보드 통신 이상 (반사 없음은 제외)
  power / heat   : 전압 부족 / 과열                    → 배터리·발열 확인

각 상황은 시작될 때 한 번 critical 알림, 해소된 상태가 HEALTH_CLEAR_SEC 이어지면 info 알림 (같은 알림 반복 없음).
"""
import subprocess
import time

import cv2
import numpy as np

import config

MESSAGES = {
    "camera_stale": ("카메라 영상이 멈춤 — 카메라 고장·분리 의심", "카메라 영상 정상화"),
    "camera_blocked": ("카메라가 가려짐 — 렌즈를 막았거나 매우 어두움", "카메라 가림 해소"),
    "stuck": ("바퀴를 움직이는데 화면이 그대로 — 끼임·들림 의심", "주행 정상화"),
    "moved": ("명령 없이 로봇이 움직임 — 들어 올리거나 밀친 것 의심", "외부 움직임 멈춤"),
    "sensor": ("초음파·모터 보드 통신 이상 — 배선·전원 확인", "센서 통신 정상화"),
    "power": ("전원 전압 부족 — 배터리 확인", "전원 전압 정상화"),
    "heat": ("라즈베리파이 과열", "온도 정상화"),
}


class HealthMonitor:
    def __init__(self, robot):
        self.r = robot
        self.active = {}            # 문제 이름 → 시작 시각
        self.since = {}             # 조건이 처음 참이 된 시각 (지속 시간 판단용)
        self.clear_since = {}       # 이상이 해소된 상태가 시작된 시각 (켜졌다 꺼졌다 반복 방지)
        self.prev_small = None
        self.next_check = 0.0
        self.next_power = 0.0

    def issues(self):
        return sorted(self.active)

    # ── 상태 갱신 ──
    def _hold(self, name, cond, now, need):
        """cond 가 need 초 이상 계속 참이면 True"""
        if not cond:
            self.since.pop(name, None)
            return False
        self.since.setdefault(name, now)
        return now - self.since[name] >= need

    def _set(self, name, on, now):
        if on:
            self.clear_since.pop(name, None)
            if name not in self.active:
                self.active[name] = now
                print(f"[health] 이상: {name}")
                self.r.alert(MESSAGES[name][0], "critical")
                self.r.send_status()
        elif name in self.active:
            # 해소된 상태가 HEALTH_CLEAR_SEC 이어져야 해소 처리
            self.clear_since.setdefault(name, now)
            if now - self.clear_since[name] < config.HEALTH_CLEAR_SEC:
                return
            self.clear_since.pop(name)
            dur = now - self.active.pop(name)
            print(f"[health] 해소: {name} ({dur:.0f}초)")
            self.r.alert(f"{MESSAGES[name][1]} ({dur:.0f}초 만에)", "info")
            self.r.send_status()

    def check(self, now):
        """메인 루프에서 매번 불러도 됨 (내부에서 0.5초 간격으로만 검사)"""
        r = self.r
        # 센서 통신: 반사 없음(None)이 아니라 보드와 통신 오류가 계속될 때만
        io_err = bool(getattr(r.hw, "io_error", False))
        if now < self.next_check:
            if io_err:
                self.since.setdefault("sensor", now)
            return
            return
        self.next_check = now + 0.5

        # 카메라 화면 멈춤
        age = r.cam.frame_age() if hasattr(r.cam, "frame_age") else 0.0
        self._set("camera_stale", self._hold("camera_stale", age > 1.0, now, config.HEALTH_CAMERA_STALE), now)

        frame = r.cam.read()
        if frame is not None:
            g = cv2.cvtColor(cv2.resize(frame, (160, 90)), cv2.COLOR_BGR2GRAY).astype(np.float32)
            # 카메라 가림: 거의 새까맣고 무늬도 없음
            dark = g.mean() < 15 and g.std() < 8
            self._set("camera_blocked", self._hold("camera_blocked", dark, now, config.HEALTH_DARK_SEC), now)

            if self.prev_small is not None and not dark:
                diff = float(np.abs(g - self.prev_small).mean())
                (sx, sy), resp = cv2.phaseCorrelate(self.prev_small, g)
                shift = (sx * sx + sy * sy) ** 0.5
                vx, _, wz = getattr(r.hw, "last_cmd", (0, 0, 0))
                driving = abs(vx) >= 1 or abs(wz) >= 1
                # 끼임·들림: 바퀴를 돌리는데 화면 변화가 거의 없음
                frozen = driving and diff < config.HEALTH_STUCK_DIFF
                self._set("stuck", self._hold("stuck", frozen, now, config.HEALTH_STUCK_SEC), now)
                # 외부 힘: 멈춤 상태인데 화면 전체가 크게 밀림 (사람이 화면을 지나가는 것과 구분하려고 응답값도 봄)
                # 바퀴 명령이나 카메라 서보가 막 바뀐 직후 1초는 제외 (카메라 화면이 늦게 따라오므로)
                settled = now - max(getattr(r.hw, "cmd_t", 0.0), getattr(r, "servo_t", 0.0)) > 1.0
                pushed = (not driving) and settled and shift > config.HEALTH_MOVED_SHIFT and resp > 0.2
                self._set("moved", self._hold("moved", pushed, now, config.HEALTH_MOVED_SEC), now)
            self.prev_small = g

        # 센서 통신
        self._set("sensor", self._hold("sensor", io_err, now, config.HEALTH_SENSOR_SEC), now)

        # 전원·온도 (10초 간격, 라즈베리파이에서만)
        if now >= self.next_power:
            self.next_power = now + 10
            throttled, temp = read_power()
            if throttled is not None:
                self._set("power", bool(throttled & 0x1), now)        # 지금 전압 부족
            if temp is not None:
                self._set("heat", temp >= config.HEALTH_TEMP_C, now)


def read_power():
    """(throttled 비트, CPU 온도) — vcgencmd 가 없으면 (None, None)"""
    try:
        t = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=2).stdout
        c = subprocess.run(["vcgencmd", "measure_temp"], capture_output=True, text=True, timeout=2).stdout
        throttled = int(t.strip().split("=")[1], 16)
        temp = float(c.strip().split("=")[1].split("'")[0])
        return throttled, temp
    except Exception:
        return None, None
