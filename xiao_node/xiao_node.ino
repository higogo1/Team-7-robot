// XIAO ESP32S3 Sense 감시 노드 — 담당: 센서 노드
// 큰 소리(마이크) 또는 BOOT 버튼 → 사진 촬영 → 관제 서버로 이벤트 전송
//
// Arduino IDE 설정
//   보드: XIAO_ESP32S3   (esp32 보드 패키지 3.x)
//   Tools > PSRAM: "OPI PSRAM"   ← 카메라 쓰려면 필수
//   안테나 꼭 연결!
//
// 팀원마다 NODE_ID 만 바꿔서 업로드 (A, B, C, D)
//
// 소리 기준값 맞추기
//   시리얼 모니터(115200)를 열면 1초마다 "level=숫자" 가 찍힌다.
//   조용할 때 값과 박수 칠 때 값을 보고 그 사이로 SOUND_THRESHOLD 를 정한다.

#include <WiFi.h>
#include <HTTPClient.h>
#include <ESP_I2S.h>
#include "esp_camera.h"

const char* WIFI_SSID = "YOUR_WIFI";          // 2.4GHz 와이파이만 됨
const char* WIFI_PASS = "YOUR_PASSWORD";
const char* SERVER    = "http://192.168.0.10:8000";  // 노트북 IP
const char* NODE_ID   = "A";

const int  SOUND_THRESHOLD   = 3000;   // 이 값 넘으면 이벤트 (현장에서 튜닝)
const unsigned long COOLDOWN_MS  = 10000;  // 이벤트 후 재감지 금지 시간
const unsigned long HEARTBEAT_MS = 10000;  // 서버에 생존 신호 주기
const int  BUTTON_PIN = 0;             // BOOT 버튼 — 시연용 수동 트리거
const int  LED_PIN    = LED_BUILTIN;   // 주황 LED (LOW 가 켜짐)

// ── XIAO ESP32S3 Sense 카메라 핀 ──
#define PWDN_GPIO_NUM  -1
#define RESET_GPIO_NUM -1
#define XCLK_GPIO_NUM  10
#define SIOD_GPIO_NUM  40
#define SIOC_GPIO_NUM  39
#define Y9_GPIO_NUM    48
#define Y8_GPIO_NUM    11
#define Y7_GPIO_NUM    12
#define Y6_GPIO_NUM    14
#define Y5_GPIO_NUM    16
#define Y4_GPIO_NUM    18
#define Y3_GPIO_NUM    17
#define Y2_GPIO_NUM    15
#define VSYNC_GPIO_NUM 38
#define HREF_GPIO_NUM  47
#define PCLK_GPIO_NUM  13

// ── 마이크 (PDM) ──
#define MIC_CLK_PIN  42
#define MIC_DATA_PIN 41

I2SClass i2s;
bool cameraOk = false;
unsigned long lastEvent = 0, lastBeat = 0, lastPrint = 0;
int maxLevelSincePrint = 0, lastSecondLevel = 0;

bool initCamera() {
  camera_config_t c = {};
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer   = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;  c.pin_d1 = Y3_GPIO_NUM;  c.pin_d2 = Y4_GPIO_NUM;  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;  c.pin_d5 = Y7_GPIO_NUM;  c.pin_d6 = Y8_GPIO_NUM;  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk = XCLK_GPIO_NUM;  c.pin_pclk = PCLK_GPIO_NUM;
  c.pin_vsync = VSYNC_GPIO_NUM;  c.pin_href = HREF_GPIO_NUM;
  c.pin_sccb_sda = SIOD_GPIO_NUM;  c.pin_sccb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn = PWDN_GPIO_NUM;  c.pin_reset = RESET_GPIO_NUM;
  c.xclk_freq_hz = 20000000;
  c.pixel_format = PIXFORMAT_JPEG;
  c.frame_size   = FRAMESIZE_VGA;      // 640x480
  c.jpeg_quality = 12;
  c.fb_count     = 2;
  c.fb_location  = CAMERA_FB_IN_PSRAM;
  c.grab_mode    = CAMERA_GRAB_LATEST; // 항상 최신 프레임
  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("카메라 초기화 실패 0x%x (PSRAM 설정 확인)\n", err);
    return false;
  }
  return true;
}

void connectWiFi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("WiFi 연결 중: %s", WIFI_SSID);
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) {
    delay(250);
    Serial.print(".");
  }
  if (WiFi.status() == WL_CONNECTED)
    Serial.printf("\n연결됨, IP: %s\n", WiFi.localIP().toString().c_str());
  else
    Serial.println("\n연결 실패 — 나중에 다시 시도");
}

// 마이크 512샘플(약 32ms)의 소리 크기 = 최대-최소 (peak-to-peak)
int readSoundLevel() {
  static int16_t buf[512];
  size_t n = i2s.readBytes((char*)buf, sizeof(buf)) / 2;
  if (n == 0) return 0;
  int16_t lo = 32767, hi = -32768;
  for (size_t i = 0; i < n; i++) {
    if (buf[i] < lo) lo = buf[i];
    if (buf[i] > hi) hi = buf[i];
  }
  return hi - lo;
}

void sendEvent(const char* type, int level) {
  connectWiFi();
  if (WiFi.status() != WL_CONNECTED) return;

  camera_fb_t* fb = nullptr;
  if (cameraOk) {
    fb = esp_camera_fb_get();
    if (!fb) Serial.println("사진 촬영 실패 — 사진 없이 전송");
  }

  String url = String(SERVER) + "/api/event?node=" + NODE_ID + "&type=" + type + "&level=" + level;
  HTTPClient http;
  http.begin(url);
  http.setTimeout(5000);
  http.addHeader("Content-Type", "image/jpeg");
  int code = fb ? http.POST(fb->buf, fb->len) : http.POST((uint8_t*)nullptr, 0);
  Serial.printf("이벤트 전송(%s, level=%d) → HTTP %d\n", type, level, code);
  http.end();
  if (fb) esp_camera_fb_return(fb);
}

void sendHeartbeat(int level) {
  if (WiFi.status() != WL_CONNECTED) { connectWiFi(); return; }
  HTTPClient http;
  http.begin(String(SERVER) + "/api/heartbeat?node=" + NODE_ID + "&level=" + level);
  http.setTimeout(2000);
  int code = http.POST((uint8_t*)nullptr, 0);
  if (code != 200) Serial.printf("heartbeat 실패 HTTP %d (서버 IP/방화벽 확인)\n", code);
  http.end();
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.printf("\n=== 감시 노드 %s ===\n", NODE_ID);
  pinMode(BUTTON_PIN, INPUT_PULLUP);
  pinMode(LED_PIN, OUTPUT);
  digitalWrite(LED_PIN, HIGH);

  cameraOk = initCamera();

  i2s.setPinsPdmRx(MIC_CLK_PIN, MIC_DATA_PIN);
  if (!i2s.begin(I2S_MODE_PDM_RX, 16000, I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO))
    Serial.println("마이크 초기화 실패");

  connectWiFi();
  for (int i = 0; i < 10; i++) readSoundLevel();  // 켜질 때 튀는 값 버리기
  sendHeartbeat(0);
}

void loop() {
  unsigned long now = millis();
  int level = readSoundLevel();
  if (level > maxLevelSincePrint) maxLevelSincePrint = level;

  if (now - lastPrint > 1000) {
    Serial.printf("level=%d (기준 %d)\n", maxLevelSincePrint, SOUND_THRESHOLD);
    lastPrint = now;
  }

  bool ready = now - lastEvent > COOLDOWN_MS || lastEvent == 0;
  bool button = digitalRead(BUTTON_PIN) == LOW;
  if (ready && (level > SOUND_THRESHOLD || button)) {
    digitalWrite(LED_PIN, LOW);
    sendEvent(button ? "button" : "sound", level);
    digitalWrite(LED_PIN, HIGH);
    lastEvent = millis();
    for (int i = 0; i < 5; i++) readSoundLevel();  // 전송 중 쌓인 소리 버리기
  }

  if (now - lastBeat > HEARTBEAT_MS) {
    sendHeartbeat(lastSecondLevel);
    lastBeat = now;
  }
}
