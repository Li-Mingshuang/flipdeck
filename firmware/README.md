# flipdeck 固件：烧录与标定

文件：`flipdeck_gamepad/flipdeck_gamepad.ino`（一个文件，Arduino IDE 直接打开）
作用：把底座的 2 个摇杆 + 11 个按键，做成标准 **BLE HID 游戏手柄**，iPhone / Android / PC 直接配对。

---

## 1. 装环境（一次性，15 分钟）

1. 装 **Arduino IDE 2.x**
2. 加 ESP32 开发板支持：`文件 → 首选项 → 附加开发板管理器网址` 填
   `https://espressif.github.io/arduino-esp32/package_esp32_index.json`
   → `工具 → 开发板 → 开发板管理器` 搜 `esp32` 安装 **2.0.x（推荐 2.0.17）**
   > ⚠️ **别用 3.x**：BLE API 变过，`ESP32 BLE Gamepad` 库在 3.x 上大概率编不过。
3. 装库：`工具 → 管理库` 搜 **ESP32 BLE Gamepad**（作者 lemmingDev）安装
   （装不上就从 GitHub 下 ZIP，用"从 ZIP 安装库"）

## 2. 烧录

1. 打开 `flipdeck_gamepad.ino`
2. 开发板选 **ESP32S3 Dev Module**；`USB CDC On Boot = Enabled`（不然串口没输出）
3. USB 插上，选对应串口 → 点上传
4. 串口监视器 115200，应看到：
   ```
   [flipdeck] booting ...
   [flipdeck] 标定摇杆中点：请松手
   [flipdeck] center  L=(2043,2051)  R=(2038,2049)
   [flipdeck] BLE name = flipdeck，等待配对…
   ```

## 3. 配对

- **iPhone 12**：`设置 → 蓝牙` → 找到 **flipdeck** → 点连接（会显示成"游戏手柄"）
- 验证：装个手柄测试 App（或直接在 Delta 模拟器里绑定按键）
- 连不上时：蓝牙里"忽略此设备"，开发板断电重上；iOS 会缓存旧的 BLE 设备名

## 4. 标定（拿到实物后做一次，5 分钟）

固件默认会在**开机时自动取摇杆中点**（开机那一秒别碰摇杆）。上下限和方向按需要微调：

1. 把 `#define DEBUG_SERIAL 1`，重新烧录，串口会每 200ms 打印原始值：
   ```
   raw LX=2043 LY=2051 RX=2038 RY=2049 | hat=0 | A=0 B=0 X=0 Y=0
   ```
2. 把摇杆推到底，记下 **最小/最大原始值**，改这两行：
   ```c
   #define ADC_MIN_RAW   200     // 推到底的最小值（比实测再往里留 50~100 更稳）
   #define ADC_MAX_RAW   3900    // 推到底的最大值
   ```
3. 方向反了 → 改 `INVERT_LX / LY / RX / RY`（左右反了改 X，上下反了改 Y）
4. 中心抖/漂 → 加大 `DEADZONE_PCT`（默认 6）或加大 `FILTER_SHIFT`（默认 2）
5. 都调好后把 `DEBUG_SERIAL` 改回 0，重新烧录

## 5. 按键对应关系（想换布局改配置区）

| 底座上的键 | GPIO | HID 编号 |
|---|---|---|
| A / B / X / Y | 6 / 7 / 8 / 9 | 1 / 2 / 3 / 4 |
| 十字键 | 12 / 13 / 14 / 15（上/下/左/右） | 帽子开关（Hat），不是独立按键 |
| Select / Start / Home | 17 / 16 / 18 | 9 / 10 / 13 |
| L1 / R1（肩键） | 21 / 38 | 5 / 6 |
| L3 / R3（摇杆按下） | 39 / 40 | 11 / 12 |
| 左摇杆 X / Y | 1 / 2 | 左摇杆轴 |
| 右摇杆 X / Y | 3 / 4 | 右摇杆轴 |
| 电池采样（可选） | 11 | 需分压 100k/100k，`REPORT_BATTERY 1` 才启用 |

> 日式手感（A/B 对调、X/Y 对调）→ 只改 `BTN_A`/`BTN_B`/`BTN_X`/`BTN_Y` 四个数字。
> 十字键用**帽子开关**上报（iOS 上游戏识别最正常）；如果你的游戏把十字键当按钮（1/2/3/4 那类），告诉我，我把 4 个开关改成独立按键上报。

## 6. 常见编译/烧录问题

| 现象 | 原因 / 处理 |
|---|---|
| `setAxes` 参数个数不对 | 库版本差异：把 `AXES_API` 改成 `6`（旧版没有 rudder），或按文件里注释改成 `setLeftThumb/setRightThumb/setHat1` |
| `setBatteryLevel` 未定义 | 老库没有这方法 → 保持 `REPORT_BATTERY 0` |
| BLE 库编译报 `NimBLE`/`BLEDevice` 相关错 | 开发板核心是 3.x → 降到 2.0.17 |
| 上传卡在 `Connecting...` | 按住 BOOT 再点上传；或换根数据线（很多线只能充电） |
| 串口没输出 | `USB CDC On Boot` 没开 |
| 配对了但游戏不认 | 先用测试 App 确认手柄在线；游戏本身不支持手柄的话，换有线也没用 |
| 摇杆一直往一边跑 | 中点没标定好 → 重开一次（开机时松手），或手动改 `ADC_CENTER_RAW` |

## 7. 接线对照（焊之前对一遍）

```
摇杆模块 5 针：VCC→3V3   GND→GND   X→对应 ADC 脚   Y→对应 ADC 脚   SW→L3/R3 脚
11 个开关    ：一端→GPIO，另一端→公共 GND（开关架上的横向总线槽就是干这个的）
主控供电     ：USB-C（原型阶段直接插线玩）；后期接锂电池 + TP4056
VBAT 采样（可选）：电池正极 → 100k → GPIO11 → 100k → GND
```

> 引脚表与 `ELECTRONICS.md` §7 一致，两边同时改就不会错。
