# DK4HD Runtime Compatibility Skill

一份面向 LLM 的独立实现规格（skill）：用可验证的行为规格描述 DK4HD 运行时兼容模块要做的事与验收标准；修复项见下表，模块不改游戏本体。

**English:** A standalone behavioral specification/skill for an LLM to implement a DK4HD runtime compatibility module from scratch: what to hook, the constants involved, and how each item is accepted. The module never modifies the game itself.

## 这个 Skill 是什么

先看装上之后玩家能感知到什么。下表是原版 DK4HD 在多数机器上的常见毛病与本模块的处理方式；规格只覆盖表中项目，不做额外扩展：

| 你会遇到 | 原版 | 装上后 | 为什么 |
|---|---|---|---|
| 画面一顿一顿，像卡在不均匀的节拍上 | 帧间隔在 31.25ms / 46.875ms 两档间跳 | p50 ≈ **33.00ms**，p95 ≈ **33.10ms** | 帧限速用的 `GetTickCount64` 只有 15.625ms 粒度；且引擎 limiter 要求 `elapsed > 33`，整数毫秒下是 34ms。补丁换成 QPC 并把 limiter 改成 `>=33` |
| 风扇狂转，一个核心被吃满 | 最热线程 ≈ 99.7% 单核 | 30Hz ≈ **7%**；Fast60 ≈ **10–12%** | 帧限速在对时间函数自旋忙等；补丁用 1ms 节流 + 帧截止前有界自旋（约 100% → 7–12%） |
| 快速点一下没反应，必须按久一点 | 5ms 点击约 2/12 生效 | 11–12/12 生效 | 鼠标每帧才采样一次（约 31–33ms），5–20ms 的点击整段落在两次采样之间 |
| 点击后偶尔只识别按下，动一下鼠标才触发抬起 | 菜单/按钮偶发“按下未弹起” | 按下沿与抬起沿成对上报 | 鼠标轮询的空闲快速路径会丢掉按下采样之后、下一次 Poll 之前发生的抬起沿 |
| 每两秒多掉一次，标题栏在 39–40 与 60 之间跳 | 每 2.6s 一次 445–450ms 的整帧阻塞 | 首次枚举结果缓存复用，`>100ms` 帧 0 次 | 游戏每 2.6s 用 `IDirectInput8::EnumDevices` 扫一遍手柄；成本随该 exe 能看到的 HID 设备数增长（单设备 20–62ms，设备多的机器整轮可达数百毫秒）；若系统里的 HID 过滤工具对该 exe 隐藏了这些设备，游戏能枚举到的设备数为 0、无需逐个打开，耗时≈0 |
| 可选：希望 60Hz 呈现并看到真实 Present/s | 固定约 30fps，无 FPS 标题 | `60fps=1`：Fast60，约 2× 游戏速度，**实测 60.0 FPS**，标题显示 Present/s；F10 实时切换 | 全局 2× QPC（先乘后除）+ limiter `>=33` + DXGI 60Hz 限速 + 帧尾自旋 + 标题监控；不做“保持原时长”的帧数补偿 |
| 可选 Fast60 的 CPU 代价 | — | 单核约 **10–12%**（30Hz 约 7%） | 为保证 33ms 截止精度，每帧最后 2ms（Fast60）/3ms（30Hz）自旋；其余时间仍 `Sleep(1)`。用 `Sleep(0)` 会让 `CpuIdle` 失效 |

默认画面、存档、玩法都不变：没有新窗口、新菜单，游戏仍是约 30fps（33ms）。`60fps=1` 是**可选 Fast60**，会让整个游戏时间轴约 2× 速，实测 PresentMon **59.9–60.0 FPS**；代价是 `CpuIdle` 帧尾自旋使单核约 10–12%。默认 `60fps=0`。

`skills.md` 是写给 LLM 的实现规格，把表中项目拆成**可验证的行为规格**：Hook 哪个函数、用哪段字节签名定位、虚表改哪个槽位、常量是多少、初始化顺序、INI/日志规则、每项怎么验收。照着做完就产出一个 x64 ASI 插件。它不是设计综述也不是教程，而是“做到什么程度才算完成”的判据。

`skills.md` 只包含接口事实、常量、签名和行为规格，不包含游戏代码、资源或实现源码。

## 如何使用

1. 将 `skills.md` 作为上下文提供给 LLM（或作为 skill/system prompt 附件）。
2. 明确要求它**只根据规格独立实现**，不要复制任何现有实现。
3. 指定目标环境：
   - Windows x64
   - Python 3.12+（仅第 9 节验收脚本需要；`pip install -r tools/requirements.txt`）
   - 游戏 `DK4HD_tc.exe` / `DK4HD_kr.exe` / `DK4HD_jp.exe` / `DK4HD_sc.exe`
   - MSYS2 MinGW-w64 + MinHook（需从源码构建）
   - Ultimate-ASI-Loader：直接用官方 x64 预编译 `version.dll`，不需要也不应该自己实现 ASI loader
4. 让 LLM 按文档顺序实现（对应 `skills.md` §2.0–2.7）：
   - `2.0` 同名 INI + 可选日志（其余功能的开关与验收载体）；
   - `2.1` 多语言签名扫描（时间函数 + 鼠标轮询，含 RVA 回退与前置校验）；
   - `2.2` QPC 帧节奏 + limiter off-by-one + 1ms 节流/帧尾自旋；
   - `2.3` 鼠标轮询 Hook + `GetDeviceState` 点击锁存；
   - `2.4` `poll+0x42` 抬起沿修复；
   - `2.5` `SkipIntro`（测试辅助，默认关闭）；
   - `2.6` 可选 `60fps=1`：Fast60（实测 60.0）+ 标题栏 FPS + F10 切换 + 方向键修复；CPU 约 10–12%；
   - `2.7` 设备枚举缓存（`EnumCache`，默认开启）。
5. 构建产物 `.asi` 与预编译 loader 一起放到游戏根目录：

   ```text
   version.dll
   DK4HD_Fix.asi
   DK4HD_Fix.ini   # 首次启动自动生成
   ```

   默认除 `SkipIntro`、`Log`、`60fps` 外全部启用（含 `EnumCache`）；`Log=0` 时不生成日志。启动游戏后按第 9 节验收。

## 适用边界

- 单机游戏互操作性修复；不绕过 DRM、不修改游戏文件、不包含游戏资源。
- 实现目标只有 `DK4HD_Fix.asi`。`version.dll` 是 Ultimate-ASI-Loader 的预编译部署依赖，不属于实现范围；不要自己写 ASI loader 或代理转发层。
- 默认仍约 30 FPS（33ms ≈ 30.3fps）。`60fps=1` 是可选 Fast60：全局 2× 时间源，实测 **59.9–60.0 FPS**，接受整体约 2 倍速；纯帧计数系统不保证 60Hz。
- Fast60 的 `CpuIdle` 帧尾自旋使单核约 **10–12%**（30Hz 约 7%）；**`Sleep(0)` 会让 `CpuIdle` 失效**，规格明确禁止用它替代自旋。
- 连续点击“按下未弹起”已纳入本规格（`ClickRelease`）；默认与 `ClickLatch` 一起开启。
- `SkipIntro=1` 仅用于测试，正常游玩保持 0；它只跳过片头/标题，不修改存档。
- `EnumCache=1` 的代价：运行中热插拔 DInput 手柄不被识别（重启即可）；XInput 手柄走 `XInputGetState`，不受影响。
- `Log=0` 是默认值：不创建、不写入同名日志；`Log=1` 才输出启动诊断日志。
- 对游戏进行互操作性修改可能受 EULA、当地法律或平台条款限制；使用前请自行确认合法性。

## 技术实现

- **帧节奏**：Hook 时间函数，用 `QueryPerformanceCounter` 毫秒值替换 `GetTickCount64` 低 32 位；同时把引擎 limiter 的 `>33` 改成 `>=33`（签名与 patch 见 `skills.md` 2.1），30Hz 从 34ms 变为 33ms。同一线程 1ms 内重复调用时 `Sleep(1)`；帧截止前 3ms 改为自旋。
- **CPU 忙等 / 定时器周期**：`CpuIdle=1` 时 `timeBeginPeriod(1)`，让 `Sleep(1)` 真正睡 1ms；帧尾自旋使 30Hz 约 7%、Fast60 约 10–12% 单核（不再是早期约 2%）。`Sleep(0)` 不保证空闲，会让 `CpuIdle` 失效。该周期覆盖整个 Hook 生命周期，在 `DLL_PROCESS_DETACH` 用 `timeEndPeriod(1)` 配对释放（仅 `timeBeginPeriod` 返回 `TIMERR_NOERROR` 时），运行中 `FreeLibrary` 也会释放。
- **点击锁存**：Hook `PeekMessageA` 捕获 `WM_LBUTTONDOWN` / `WM_LBUTTONDBLCLK`，在下一次 `GetDeviceState` 给 `DIMOUSESTATE.rgbButtons[0]` 置位一次。
- **抬起沿修复**：鼠标轮询 `+0x42` 的 `75 4E` 改为 `EB 4E`，强制走普通路径。
- **DInput 虚表**：只原地修改原始虚表的 `EnumDevices`（槽位 4）与 `GetDeviceState`（槽位 9），不复制虚表、不替换设备 vptr。
- **EnumCache**：首次 `EnumDevices` 成功后按 `(dwDevType, dwFlags)` 缓存最多 4 键 × 16 设备；之后回放，失败或设备数溢出时回落真实枚举。
- **Fast60**：`60fps=1` 时时间源全局 ×2（**先乘后除** `qpc*2000/freq`，保留 0.5ms 墙钟分辨率）；必须同时 patch limiter `>33→>=33` 并在帧截止前 4 个游戏毫秒（2ms 墙钟）自旋，才能到 60.0；再叠加 DXGI Present 60Hz 限速 + 标题栏 Present/s；F10 实时切换；任一 Hook 失败则全有或全无（limiter patch 失败例外，回退 58.8 并写日志）。

## 文件

```text
README.md          # 本说明
skills.md          # 完整行为规格 / skill
tools/
  _dk4.py             # 共享 Windows/INI/日志/DPI/实例检查辅助
  check_startup.py    # 9.1 启动与 Hook
  measure_frames.py   # 9.2 帧时间
  measure_cpu.py      # 9.3 CPU
  click_test.py       # 9.4 短点击锁存（trace 构建）
  click_release_test.py # 9.6 按下/抬起沿配对（Frida）
  test_config.py      # 9.7 INI/日志/SkipIntro
  fast60_test.py      # 9.9 Fast60 + 标题栏/F10/四语言 + limiter 日志与 16.9/17.5/59.5 性能门禁
  enumcache_test.py   # 9.10 设备枚举缓存 + 外部 HID 过滤工具检测 + 前台 >100ms 帧门禁 + A/B
  requirements.txt    # Python 依赖（frida / psutil / Pillow）
```

本仓库只提供规格与验收脚本，不包含实现源码；实现由 LLM 根据 `skills.md` 独立完成。

第三方组件（Ultimate-ASI-Loader、MinHook、PresentMon）遵循各自许可证。
