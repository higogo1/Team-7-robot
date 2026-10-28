// XIAO ESP32S3 Sense 감시 노드 — 담당: 센서 노드
// 큰 소리(마이크) 또는 버튼 → 사진 촬영 → 관제 서버로 이벤트 전송
//
// Arduino IDE 설정
//   보드: XIAO_ESP32S3   (esp32 보드 패키지 3.x)
//   Tools > PSRAM: "OPI PSRAM"   ← 카메라 쓰려면 필수
//   안테나 꼭 연결!
//
// 팀원마다 NODE_ID 만 바꿔서 업로드 (A, B, C, D)

#include <WiFi.h>

const char* WIFI_SSID = "YOUR_WIFI";          // 2.4GHz 와이파이만 됨
const char* WIFI_PASS = "YOUR_PASSWORD";
const char* SERVER    = "http://192.168.0.10:8000";  // 노트북 IP
const char* NODE_ID   = "A";

void setup() {
  Serial.begin(115200);
  // TODO: 카메라 초기화, 마이크 초기화, WiFi 연결
}

void loop() {
  // TODO: 소리 크기 측정 → 기준 넘으면 사진 찍어서 POST /api/event?node=A&type=sound
}
