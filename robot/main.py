"""라즈봇 메인 — 담당: 로봇 주행

모드
  idle     : 정지
  patrol   : 라인을 따라 루프를 돌며 체크포인트마다 잠깐 멈춤
  dispatch : 목표 체크포인트까지 이동 후 정지
  follow   : 등록된 사람을 따라감 (vision.py 사용)

실행: python main.py
"""


class Robot:
    def __init__(self):
        self.mode = "idle"

    def connect(self):
        # TODO: 관제 서버(config.SERVER_URL)에 웹소켓 연결
        pass

    def on_message(self, ws, raw):
        # TODO: 서버 명령 처리 (patrol / follow / stop / dispatch / register)
        pass

    def line_step(self):
        # TODO: 라인 추종 + 체크포인트(가로 테이프) 카운트
        pass

    def follow_step(self):
        # TODO: 등록된 사람 위치에 맞춰 회전/전진
        pass

    def run(self):
        # TODO: 메인 루프 — 모드별 step 호출 + 상태 보고
        pass


if __name__ == "__main__":
    Robot().run()
