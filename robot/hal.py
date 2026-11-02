"""라즈봇 V2 하드웨어 래퍼 — 담당: 로봇 주행

Yahboom 자료의 Raspbot_Lib.py 를 이 폴더(robot/)에 복사해서 사용.
파일이 없으면(노트북에서 테스트할 때) 가짜 하드웨어로 동작하고 명령만 출력한다.

레지스터/모터 번호는 Yahboom Raspbot V2 예제 기준:
  모터  0=왼앞 1=왼뒤 2=오앞 3=오뒤,  Ctrl_Car(id, dir(0앞/1뒤), speed 0~255)
  라인  read_data_array(0x0a, 1) → 비트3=왼바깥 비트2=왼안 비트1=오안 비트0=오바깥
  초음파 Ctrl_Ulatist_Switch(1) 후 0x1b(상위), 0x1a(하위) → mm
  서보  Ctrl_Servo(1, 좌우 0~180), Ctrl_Servo(2, 상하 0~110)
"""
import time

import config

try:
    from Raspbot_Lib import Raspbot
except ImportError:
    Raspbot = None


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class Hardware:
    def __init__(self):
        self.fake = Raspbot is None
        if self.fake:
            print("[hal] Raspbot_Lib.py 없음 → 가짜 하드웨어 모드")
            self.bot = None
        else:
            self.bot = Raspbot()
            self.bot.Ctrl_Ulatist_Switch(1)
        self._last = None
        self.last_cmd = (0.0, 0.0, 0.0)  # 마지막 주행 명령 (vx, vy, wz) — 대시보드 작동 표시용
        self.cmd_t = 0.0                 # 주행 명령이 마지막으로 바뀐 시각 (기기 이상 감시용)
        self.io_error = False            # 마지막 센서 읽기에서 보드와 통신 오류가 났는지 (기기 이상 감시용)

    def drive(self, vx, vy=0.0, wz=0.0):
        """메카넘 주행. vx: 전진+, vy: 왼쪽 이동+, wz: 좌회전+  (단위: 모터 값, 대략 -255~255)"""
        if (vx, vy, wz) != self.last_cmd:
            self.cmd_t = time.time()
        self.last_cmd = (vx, vy, wz)
        speeds = [
            vx - vy - wz,  # 왼앞
            vx + vy - wz,  # 왼뒤
            vx + vy + wz,  # 오앞
            vx - vy + wz,  # 오뒤
        ]
        speeds = [int(_clamp(s * sign, -255, 255)) for s, sign in zip(speeds, config.MOTOR_SIGN)]
        if speeds == self._last:
            return  # 같은 값 반복 전송 방지 (I2C 부하 줄이기)
        self._last = speeds
        if self.fake:
            print(f"[hal] motors {speeds}")
            return
        for i, s in enumerate(speeds):
            self.bot.Ctrl_Car(i, 0 if s >= 0 else 1, abs(s))

    def stop(self):
        self.drive(0, 0, 0)

    def read_line(self):
        """바닥 라인센서 4개 → [왼바깥, 왼안, 오안, 오바깥], True면 검정선 위"""
        if self.fake:
            return [False, True, True, False]
        raw = int(self.bot.read_data_array(0x0a, 1)[0])
        bits = [(raw >> 3) & 1, (raw >> 2) & 1, (raw >> 1) & 1, raw & 1]
        return [(b == 0) if config.LINE_ACTIVE_LOW else (b == 1) for b in bits]

    def distance_cm(self):
        """초음파 거리(cm). 반사가 안 돌아오면(앞이 트임) None.
        보드와 통신 자체가 안 되면 io_error=True (반사 없음과 구분)"""
        if self.fake:
            return 100.0
        hi = self.bot.read_data_array(0x1b, 1)   # Raspbot_Lib 은 I2C 오류 시 예외 대신 None 을 돌려줌
        lo = self.bot.read_data_array(0x1a, 1)
        if not hi or not lo:
            self.io_error = True
            return None
        self.io_error = False
        mm = (hi[0] << 8) | lo[0]
        return mm / 10.0 if mm > 0 else None

    def servo(self, pan, tilt):
        """카메라 짐벌 각도"""
        if self.fake:
            return
        self.bot.Ctrl_Servo(1, int(_clamp(pan, 0, 180)))
        self.bot.Ctrl_Servo(2, int(_clamp(tilt, 0, 110)))

    def pan(self, angle):
        """카메라 좌우만 (90: 정면, 클수록 왼쪽)"""
        if self.fake:
            return
        self.bot.Ctrl_Servo(1, int(_clamp(angle, 0, 180)))

    def tilt(self, angle):
        """카메라 상하만 (0: 바닥 쪽, 클수록 위)"""
        if self.fake:
            return
        self.bot.Ctrl_Servo(2, int(_clamp(angle, 0, 110)))

    def close(self):
        self.stop()
        if not self.fake:
            self.bot.Ctrl_Ulatist_Switch(0)
