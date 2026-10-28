"""라즈봇 V2 하드웨어 래퍼 — 담당: 로봇 주행

Yahboom 자료의 Raspbot_Lib.py 를 이 폴더에 복사해서 사용.
(모터/라인센서/초음파/서보 사용법은 Yahboom 예제 참고)
"""


class Hardware:
    def drive(self, vx, vy=0.0, wz=0.0):
        """메카넘 주행. vx: 전진+, vy: 왼쪽 이동+, wz: 좌회전+"""
        # TODO
        pass

    def stop(self):
        self.drive(0, 0, 0)

    def read_line(self):
        """바닥 라인센서 4개 → [왼바깥, 왼안, 오안, 오바깥], True면 검정선 위"""
        # TODO
        pass

    def distance_cm(self):
        """초음파 거리(cm)"""
        # TODO
        pass

    def servo(self, pan, tilt):
        """카메라 짐벌 각도"""
        # TODO
        pass
