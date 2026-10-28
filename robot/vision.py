"""특정인 얼굴 등록/인식 — 담당: 로봇 비전

OpenCV 내장 YuNet(얼굴 검출) + SFace(얼굴 인식) 사용 예정.
모델 파일은 robot/models/ 에 둔다.
"""


class FaceID:
    def detect(self, frame):
        """프레임에서 얼굴 목록 검출"""
        # TODO
        pass

    def register(self, frame):
        """화면의 얼굴을 따라갈 사람으로 저장"""
        # TODO
        pass

    def find_target(self, frame, faces):
        """등록된 사람 얼굴 찾기"""
        # TODO
        pass
