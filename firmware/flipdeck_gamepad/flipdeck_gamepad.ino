/* ============================================================================
 *  flipdeck 蓝牙手柄固件  (ESP32-S3 + 2 摇杆 + 11 按键)
 *  ---------------------------------------------------------------------------
 *  作用：把 flipdeck 底座里的摇杆和按键，伪装成一个标准 BLE HID 游戏手柄，
 *        iPhone / iPad / Android / PC 直接配对使用（iPhone 12 走蓝牙即可，无需 MFi）。
 *
 *  依赖（Arduino IDE）：
 *    1) 开发板支持：esp32 by Espressif  —— **请用 2.0.x（如 2.0.17）**，3.x 的 BLE API 变了可能编不过
 *    2) 库：ESP32 BLE Gamepad (lemmingDev)  —— 库管理器搜 "ESP32 BLE Gamepad"
 *
 *  烧录设置：Board = "ESP32S3 Dev Module"，USB CDC On Boot = Enabled，串口 115200
 * ==========================================================================*/

#include <BleGamepad.h>

// ============================== 配置区（照着改） ==============================

#define DEVICE_NAME        "flipdeck"   // 蓝牙里显示的名字
#define REPORT_HZ          100          // 上报频率 Hz
#define AXES_API           8            // 8 = 新版库 setAxes(8个参数)；6 = 旧版库(去掉 rudder)
#define REPORT_BATTERY     0            // 1 = 上报电量（需要接 VBAT 分压；老库若无此方法就保持 0）
#define DEBUG_SERIAL       0            // 1 = 串口打印摇杆原始值（标定用），正常玩时设 0
#define AUTO_CENTER_ON_BOOT 1           // 开机自动取摇杆中点（值越大越省事，也越依赖开机时手别碰摇杆）
#define INVERT_LY          1            // 左摇杆 Y 反向（一般都要 1）
#define INVERT_RY          1            // 右摇杆 Y 反向
#define INVERT_LX          0
#define INVERT_RX          0
#define DEADZONE_PCT       6            // 摇杆中心死区（%）
#define FILTER_SHIFT       2            // 摇杆低通滤波：2 = 4 次平均(推荐)，0 = 不滤波

// ---- 引脚（与 ELECTRONICS.md §7 引脚表一致，改线就改这里） ----
#define PIN_LX   1      // 左摇杆 X   (ADC1)
#define PIN_LY   2      // 左摇杆 Y   (ADC1)
#define PIN_RX   3      // 右摇杆 X   (ADC1)
#define PIN_RY   4      // 右摇杆 Y   (ADC1)

#define PIN_A    6
#define PIN_B    7
#define PIN_X    8
#define PIN_Y    9

#define PIN_DU  12      // 十字键 上
#define PIN_DD  13      // 十字键 下
#define PIN_DL  14      // 十字键 左
#define PIN_DR  15      // 十字键 右

#define PIN_START  16
#define PIN_SELECT 17
#define PIN_HOME   18
#define PIN_L1     21   // 左肩
#define PIN_R1     38   // 右肩
#define PIN_L3     39   // 左摇杆按下
#define PIN_R3     40   // 右摇杆按下

#define PIN_VBAT   11   // 电池电压采样（分压 100k/100k），不用就留空

// ---- 按键 -> HID 编号（想换布局只改这里） ----
// iOS/Android 上常见的对应关系：1=A 2=B 3=X 4=Y 5=L1 6=R1 7=L2 8=R2
//                              9=Select 10=Start 11=L3 12=R3 13=Home
#define BTN_A      1
#define BTN_B      2
#define BTN_X      3
#define BTN_Y      4
#define BTN_START  10
#define BTN_SELECT 9
#define BTN_HOME   13
#define BTN_L1     5
#define BTN_R1     6
#define BTN_L3     11
#define BTN_R3     12

// ---- 摇杆原始值范围（12bit ADC）----
// 如果 AUTO_CENTER_ON_BOOT=1，中点会自动测；上下限按你的模块实测微调
#define ADC_MIN_RAW   200
#define ADC_MAX_RAW   3900
#define ADC_CENTER_RAW 2048

#ifndef HAT_CENTERED
#define HAT_CENTERED   0
#define HAT_UP         1
#define HAT_UP_RIGHT   2
#define HAT_RIGHT      3
#define HAT_DOWN_RIGHT 4
#define HAT_DOWN       5
#define HAT_DOWN_LEFT  6
#define HAT_LEFT       7
#define HAT_UP_LEFT    8
#endif

// ============================== 实现 ==============================

BleGamepad bleGamepad(DEVICE_NAME, "flipdeck DIY", 100);

struct Axis {
  uint8_t pin;
  int center, lo, hi;
  bool invert;
  float filt;
};

Axis axLX = {PIN_LX, ADC_CENTER_RAW, ADC_MIN_RAW, ADC_MAX_RAW, INVERT_LX, 0};
Axis axLY = {PIN_LY, ADC_CENTER_RAW, ADC_MIN_RAW, ADC_MAX_RAW, INVERT_LY, 0};
Axis axRX = {PIN_RX, ADC_CENTER_RAW, ADC_MIN_RAW, ADC_MAX_RAW, INVERT_RX, 0};
Axis axRY = {PIN_RY, ADC_CENTER_RAW, ADC_MIN_RAW, ADC_MAX_RAW, INVERT_RY, 0};

struct Button { uint8_t pin; uint8_t hid; const char *name; };
Button buttons[] = {
  {PIN_A, BTN_A, "A"}, {PIN_B, BTN_B, "B"}, {PIN_X, BTN_X, "X"}, {PIN_Y, BTN_Y, "Y"},
  {PIN_L1, BTN_L1, "L1"}, {PIN_R1, BTN_R1, "R1"},
  {PIN_START, BTN_START, "Start"}, {PIN_SELECT, BTN_SELECT, "Select"}, {PIN_HOME, BTN_HOME, "Home"},
  {PIN_L3, BTN_L3, "L3"}, {PIN_R3, BTN_R3, "R3"},
};
const int N_BUTTONS = sizeof(buttons) / sizeof(buttons[0]);

// ---------------------------------------------------------------- 摇杆
void axisBegin(Axis &a) { pinMode(a.pin, INPUT); }

int axisRaw(Axis &a) {
  int v = analogRead(a.pin);
  if (FILTER_SHIFT > 0) {                       // 简单低通，压抖动
    a.filt += ((float)v - a.filt) / (float)(1 << FILTER_SHIFT);
    v = (int)(a.filt + 0.5f);
  }
  return v;
}

int axisValue(Axis &a, int raw) {
  int v;
  if (raw >= a.center) v = map(raw, a.center, a.hi, 0, 127);
  else                 v = map(raw, a.lo, a.center, -127, 0);
  v = constrain(v, -127, 127);
  if (abs(v) < (DEADZONE_PCT * 127 / 100)) v = 0;      // 死区
  if (a.invert) v = -v;
  return v;
}

void axisCalibrateCenter(Axis &a) {           // 开机取中点：手别碰摇杆
  long sum = 0;
  for (int i = 0; i < 32; i++) { sum += analogRead(a.pin); delay(2); }
  a.center = (int)(sum / 32);
  a.filt = a.center;
}

// ---------------------------------------------------------------- 按键
void buttonsBegin() {
  for (int i = 0; i < N_BUTTONS; i++) pinMode(buttons[i].pin, INPUT_PULLUP);
  pinMode(PIN_DU, INPUT_PULLUP); pinMode(PIN_DD, INPUT_PULLUP);
  pinMode(PIN_DL, INPUT_PULLUP); pinMode(PIN_DR, INPUT_PULLUP);
}

bool pressed(uint8_t pin) { return digitalRead(pin) == LOW; }   // 按键另一端接 GND

uint8_t readHat() {
  bool u = pressed(PIN_DU), d = pressed(PIN_DD), l = pressed(PIN_DL), r = pressed(PIN_DR);
  if (u && l) return HAT_UP_LEFT;
  if (u && r) return HAT_UP_RIGHT;
  if (d && l) return HAT_DOWN_LEFT;
  if (d && r) return HAT_DOWN_RIGHT;
  if (u) return HAT_UP;
  if (d) return HAT_DOWN;
  if (l) return HAT_LEFT;
  if (r) return HAT_RIGHT;
  return HAT_CENTERED;
}

#if REPORT_BATTERY
void reportBattery() {
  // 100k/100k 分压：VBAT 4.2V -> 2.1V -> ADC
  int raw = analogRead(PIN_VBAT);
  float v = raw * 3.3f / 4095.0f * 2.0f;
  int pct = (int)constrain((v - 3.30f) / (4.20f - 3.30f) * 100.0f, 0.0f, 100.0f);
  bleGamepad.setBatteryLevel((uint8_t)pct);
}
#endif

// ---------------------------------------------------------------- 主流程
void setup() {
  Serial.begin(115200);
  delay(200);
  Serial.println("\n[flipdeck] booting ...");

  analogReadResolution(12);
  axisBegin(axLX); axisBegin(axLY); axisBegin(axRX); axisBegin(axRY);
  buttonsBegin();

#if AUTO_CENTER_ON_BOOT
  Serial.println("[flipdeck] 标定摇杆中点：请松手");
  delay(400);
  axisCalibrateCenter(axLX); axisCalibrateCenter(axLY);
  axisCalibrateCenter(axRX); axisCalibrateCenter(axRY);
  Serial.printf("[flipdeck] center  L=(%d,%d)  R=(%d,%d)\n",
                axLX.center, axLY.center, axRX.center, axRY.center);
#endif

  bleGamepad.begin();
  Serial.printf("[flipdeck] BLE name = %s，等待配对…\n", DEVICE_NAME);
}

void loop() {
#if DEBUG_SERIAL
  static uint32_t tDbg = 0;
  if (millis() - tDbg > 200) {
    tDbg = millis();
    Serial.printf("raw LX=%4d LY=%4d RX=%4d RY=%4d | hat=%d | A=%d B=%d X=%d Y=%d\n",
                  analogRead(PIN_LX), analogRead(PIN_LY), analogRead(PIN_RX), analogRead(PIN_RY),
                  readHat(), pressed(PIN_A), pressed(PIN_B), pressed(PIN_X), pressed(PIN_Y));
  }
#endif

  static uint32_t tLast = 0;
  const uint32_t period = 1000 / REPORT_HZ;
  if (millis() - tLast < period) { delay(1); return; }
  tLast = millis();

  if (!bleGamepad.isConnected()) return;

  int lx = axisValue(axLX, axisRaw(axLX));
  int ly = axisValue(axLY, axisRaw(axLY));
  int rx = axisValue(axRX, axisRaw(axRX));
  int ry = axisValue(axRY, axisRaw(axRY));
  uint8_t hat = readHat();

#if AXES_API == 8
  bleGamepad.setAxes(lx, ly, rx, ry, 0, 0, 0, hat);
#else
  bleGamepad.setAxes(lx, ly, rx, ry, 0, 0, hat);
#endif
  // 如果你的库版本没有 setAxes，改成下面三行：
  //   bleGamepad.setLeftThumb(lx, ly);
  //   bleGamepad.setRightThumb(rx, ry);
  //   bleGamepad.setHat1(hat);

  for (int i = 0; i < N_BUTTONS; i++) {
    if (pressed(buttons[i].pin)) bleGamepad.press(buttons[i].hid);
    else                         bleGamepad.release(buttons[i].hid);
  }

#if REPORT_BATTERY
  static uint32_t tBat = 0;
  if (millis() - tBat > 10000) { tBat = millis(); reportBattery(); }
#endif
}
