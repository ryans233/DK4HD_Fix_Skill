# DK4HD Runtime Compatibility Skill

> 目标：让另一个 LLM / 开发者**仅根据本文档描述的行为与接口事实**，独立实现一个 DK4HD 的运行时兼容模块。  
> 本文档不包含游戏代码、资源或内部实现细节；只包含互操作性所需的接口事实、常量、签名、行为规格和验证方法。

---

## 0. 法律与边界（先读）

- 该模块是**单机游戏的非官方互操作性修复**，不绕过 DRM、不修改游戏文件、不提供游戏资源。
- 对游戏进行互操作性修改可能受游戏 EULA、当地法律或平台条款限制。使用/实现前请自行确认合法性；本文档不是法律意见。
- **独立实现规则**：不要复制游戏代码、资源或内部实现细节；只依据本文档描述的接口事实与行为规格实现。第三方库遵循各自许可证。
- 不要分发 `DK4HD_*.exe`、`*.dll`（游戏本体）、存档、贴图、音频或任何 Koei Tecmo 资产。
- `version.dll` 来自 Ultimate-ASI-Loader（MIT）；MinHook 为 BSD 2-Clause；PresentMon 仅测试工具。发布时附各自许可证。
- 仅用于单机；不要用于联机/反作弊环境。

---

## 1. 目标产物

**唯一需要实现的交付物**是一个 x64 ASI 插件：

```text
version.dll        # 部署依赖：Ultimate-ASI-Loader x64 预编译二进制，原样使用，不实现
DK4HD_Fix.asi # 独立实现的 x64 DLL（名称可自定；下同）
DK4HD_Fix.ini # 首次启动自动生成；同名 INI（见 2.5）
```

- 首次启动在同目录生成与 ASI 同基名的 `.ini`；`Log=0`（默认）不生成 `.log`，`Log=1` 才写同名日志（见 2.5）。

- `version.dll` **不是**实现目标。直接取 Ultimate-ASI-Loader 官方 release 的 x64 `version.dll` 复制到游戏根目录；不需要自己编译、重写或逆向 loader。
- **不要**自行实现 ASI loader / `version.dll` 代理：本文档未提供导出转发、ASI 目录扫描、加载时序等规格，loader 因此不可按本文档验收，也不在独立实现范围内。
- 若环境里已有其他**预编译** ASI loader（其他代理 DLL 名），可直接复用；这属于部署选择，不是实现任务。
- 成功判定只看 `DK4HD_Fix.asi` 的行为（第 2 节 + 第 9 节）；loader 只需满足“游戏能加载它、它能发现并加载 `.asi`”这一既有事实。

安装：两个文件放在游戏根目录（与 `DK4HD_tc.exe` 同级）。插件导出：

```c
extern "C" __declspec(dllexport) void InitializeASI(void);
```

`DllMain(DLL_PROCESS_ATTACH)` 只做 `DisableThreadLibraryCalls`，不要在 `DllMain` 里做重活。

支持的 exe：

| exe | 时间函数 RVA | 鼠标轮询 RVA（1.0.1） | 鼠标轮询 RVA（1.0.2） |
|---|---|---|---|
| `DK4HD_tc.exe` | `0xD6630` | `0x2B5B10` | `0x2B5BC0` |
| `DK4HD_kr.exe` | `0xD8B10` | `0x2BD7F0` | `0x2BD8A0` |
| `DK4HD_jp.exe` | `0xD6330` | `0x2B5830` | `0x2B58E0` |
| `DK4HD_sc.exe` | `0xD62F0` | `0x2B57D0` | `0x2B5880` |

> RVA 基于 ImageBase `0x140000000`；运行时地址 = exe 模块基址 + RVA。优先扫描签名，RVA 表仅作回退。表内值为函数入口，回退时仍必须通过第 2.3 节的前缀校验。时间函数两版 RVA 相同；鼠标轮询在 1.0.1/1.0.2 间后移 `0xB0`，回退时按 exe 名依次尝试两个版本，命中并通过校验的那个才使用；全部失败才安全退出（第 9.5 节）。

---

## 2. 必须实现的修复行为

### 2.1 帧节奏：QPC 时间源 + 1ms 节流

**原始行为（需要替换）**

- 主循环使用 `GetTickCount64()` 的低 32 位作为毫秒时间源。
- `GetTickCount64` 默认粒度约 15.625ms；33ms 帧限速会被量化为 31.25ms / 46.875ms 两档，产生 31/47ms 抖动。
- 帧限速对时间函数存在高频自旋，导致单核接近 100%。

**目标行为**

- 默认 `FramePacing=1`、`CpuIdle=1`：Hook 时间函数，返回 QPC 毫秒值，不调用原函数。
- 每线程维护 `lastQpc`：同一线程两次调用间隔 < 1ms 时 `Sleep(1)`，然后重新读取 QPC。
- `InitializeASI` 中调用 `timeBeginPeriod(1)`（仅在 `CpuIdle=1` 时需要）。
- 返回 `uint32_t`（调用方使用低 32 位），QPC 毫秒值按 2^32 取模。

**配置开关**

| 组合 | 行为 |
|---|---|
| `FramePacing=1, CpuIdle=1` | 默认：QPC 返回值 + 1ms 节流 |
| `FramePacing=1, CpuIdle=0` | 只替换为 QPC，不做 `Sleep(1)`（帧节奏稳定，但忙等仍在） |
| `FramePacing=0, CpuIdle=1` | 仍 Hook；先做 1ms 节流，再调用原时间函数并返回原值（只降 CPU，不改时间源） |
| `FramePacing=0, CpuIdle=0` | 不 Hook 时间函数，完全保持原版 |

伪代码（默认组合；其他组合按上表分支）：

```c
uint64_t HookTime(void* arg) {
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);

    static thread_local LARGE_INTEGER last = {};
    if (cpuIdle && last.QuadPart && (now.QuadPart - last.QuadPart) < ticksPerMs) {
        Sleep(1);
        QueryPerformanceCounter(&now);
    }
    last = now;

    if (!framePacing)
        return origTime(arg);          // 原时间函数（来自 Hook trampoline）

    uint64_t ms = (now.QuadPart * 1000ULL) / qpcFreq.QuadPart;
    return ms & 0xFFFFFFFFULL;
}
```

- `ticksPerMs = qpcFreq / 1000`；若为 0 则设为 1。
- **顺序要求**：QPC 频率、`ticksPerMs`、`timeBeginPeriod(1)` 必须在启用 Hook 前设置好，否则 Hook 可能除以 0。
- 该 Hook 必须只替换时间函数；不要修改 `GetTickCount64` 本身。
- 关闭时间 Hook（两开关都为 0）时，不要创建/启用对应 MinHook。
- 上述 `last` 节流只适用于 30Hz/原时间源路径；Fast60 的 `CpuIdle` 节流必须使用 2.7 的独立 `lastCalib`，**不得复用** `last`。

**验收**

- PresentMon 15s：`msBetweenPresents` p50 ≈ 33.0–33.1ms，p95 ≈ 35.5–35.8ms，`SyncInterval=1`。
- 最热线程 CPU ≈ 1.5–2.3% 单核（原版约 99.7%）。
- 默认仍约 30fps；**不要**通过改 33ms tick 解锁 60fps。可选实验性 Fast60 见 2.7，必须默认关闭。

### 2.2 短点击：消息锁存 + GetDeviceState 注入

**问题**

- 游戏每帧只调用一次 `IDirectInputDevice8::Poll` + `GetDeviceState` 读取鼠标即时状态，采样间隔约 31–33ms。
- 5–20ms 的点击可能整段落在两次采样之间，游戏看不到。

**目标行为**

1. Hook `PeekMessageA`（仅进程内 user32）：
   - 调用原函数。
   - 若返回 `TRUE` 且消息为 `WM_LBUTTONDOWN`（0x0201）或 `WM_LBUTTONDBLCLK`（0x0203），且 `GetWindowThreadProcessId(msg->hwnd) == GetCurrentProcessId()`：
     - 记录 `pendingTime = GetTickCount64()`；
     - 设置 `pending = true`。
   - 不阻塞、不修改消息。

2. Hook 鼠标轮询函数（见第 3 节），在首次调用时安装 DInput 虚表 Hook：
   - 从轮询对象偏移 `+0x10` 读取 `IDirectInputDevice8*`；
   - 读取其虚表；
   - 保存 `vt[0x48/8]` 原始 `GetDeviceState`；
   - **原地**把 `vt[0x48/8]` 改为自定义函数；**不要复制虚表，不要修改设备 vptr**。

3. 自定义 `GetDeviceState(self, cb, data)`：
   - 先调用原始 `GetDeviceState`；
   - 若 `SUCCEEDED(hr)`、`data != null`、`cb >= sizeof(DIMOUSESTATE)`（20 字节）、`self == 目标鼠标设备`：
     - `bool had = pending.exchange(false)`（无论是否过期都清除）；
     - 若 `had` 且 `GetTickCount64() - pendingTime <= 1500`：
       - 将 `DIMOUSESTATE.rgbButtons[0]` 按位或 `0x80`（左键）。
       - 即使物理左键已经按下也可以置位，保持幂等。
   - 返回原始 HRESULT。

**关键行为**

- 注入只持续**一次** `GetDeviceState`；下一帧返回真实状态。只有同时启用 2.4 的 `ClickRelease`，游戏才能稳定得到完整的“按下沿 → 抬起沿”。
- 不开启 `DIPROP_BUFFERSIZE`，不调用 `GetDeviceData`。
- 不注入鼠标移动、不发送 `SendInput`、不修改光标位置。
- 不对键盘/手柄设备注入；只对首次捕获的鼠标设备。

**验收**

- 5/10/20/40/80ms 点击各 12 次，通常每档 12/12；至少 5ms 点击从约 2/12 提升到 11–12/12。
- 不出现重复按下沿（12 次点击不应变成 20+ 次）。
- `ClickLatch` 必须与 2.4 的 `ClickRelease` 一起开启；单独锁存只补“按下”，可能重现“按下未弹起”。

### 2.3 多语言定位：签名扫描，RVA 回退

不要在 `InitializeASI` 里硬编码 TC 的 RVA。按以下顺序定位：

**时间函数**

1. 在 exe 的 `.text` 中搜索唯一字节串：

```text
48 B9 00 00 00 00 01 00 00 00 48 F7 F1
```

即 `mov rcx,100000000h; div rcx`。

2. 若命中地址为 `hit`，候选函数入口 `cand = hit - 0x26`。
3. 校验 `cand` 处的 18 字节前缀：

```text
48 89 4C 24 08 57 48 83 EC 20 48 8B FC B9 08 00 00 00
```

4. 通过则使用 `cand`；否则继续搜索下一个 `div` 命中。

> 不要用 18 字节前缀单独搜索：它在这些 exe 中会命中约 2114 次，是通用 MSVC RTC 序言，不是唯一签名。

**鼠标轮询**

在 `.text` 中搜索唯一 25 字节序列：

```text
48 89 5C 24 10 57 48 83 EC 30 48 8B D9 48 8B 49 10 48 8B 01 FF 90 C8 00 00 00
```

命中地址就是函数入口。校验前 13 字节：

```text
48 89 5C 24 10 57 48 83 EC 30 48 8B D9
```

**扫描实现**

- 从 PE 头读取 `.text` 的 `VirtualAddress` / `Misc.VirtualSize`，扫描 `g_base + VirtualAddress` 起的内存。
- 逐字节 `memcmp`；只接受同时满足签名与前置校验的命中。
- 扫描失败时按 exe 文件名回退到第 1 节 RVA 表，并再次校验签名；仍失败则写日志并**不要**安装 Hook 或修改内存。
- 扫描/校验失败应安全退出，不崩溃、不修改错误地址。

### 2.4 抬起沿修复：绕过鼠标轮询的空闲快速路径

**问题**

鼠标轮询函数在“上一帧基线全 0 且 `Poll()==1`（`DI_NOEFFECT`）”时走空闲快速路径。该判断读的是 `+0x58` 的**采样前基线**，而不是上一次 `GetDeviceState` 实际返回的 `+0x70`。如果按下被采样后、下一次 `GetDeviceState` 之前已经抬起：

1. 基线仍是 0（按下帧保存的是采样前的空闲值）；
2. 快速路径读到全 0 状态后返回“无效”，既不更新基线，也不让上层执行边沿判定；
3. 游戏层 `G+0x60`（逻辑左键）保持 1，UI 认为左键一直按住；
4. 鼠标移动后 `lX/lY` 非 0，快速路径失效，上层用仍为 0 的基线与当前 0 按键算出空闲沿，才清掉 `G+0x60`，UI 这时才收到抬起。

**目标行为**

- 在鼠标轮询函数入口 `+0x42` 处把 `75 4E`（`jnz`）改成 `EB 4E`（`jmp`），强制每帧走普通路径。
- 写入前必须校验目标处正好是 `75 4E`；不匹配则写日志并跳过，不要猜地址。
- 该偏移在 1.0.1 / 1.0.2 的 TC/KR/JP/SC 八个 exe 中一致；仍需先按 2.3 扫描到鼠标轮询入口。
- `ClickRelease=1` 时应用；`0` 时不得修改。
- 该补丁与 `ClickLatch` 独立：只开 `ClickRelease` 时短点击仍可能丢；只开 `ClickLatch` 时可能重现“按下未弹起”；推荐两者都开（默认）。

**验收**

- 默认（`ClickLatch=1` + `ClickRelease=1`）：5ms 点击的按下沿与抬起沿数量相等，结束后左键状态为 0。
- 对照（`ClickLatch=1` + `ClickRelease=0`）：抬起沿明显少于按下沿，左键状态卡在 1；移动鼠标后才恢复。
- 脚本：`tools/click_release_test.py`（第 9.6 节）。

### 2.5 同名 INI 配置 + 可选日志

**配置文件**

- 从 ASI 自身模块路径推导：同目录、同基名、扩展名 `.ini`。例如 `DK4HD_Fix.asi` → `DK4HD_Fix.ini`。不要让 INI 文件名与 ASI 名脱钩。
- 文件不存在时创建默认 INI；已存在时**不要覆盖**，只读取；缺失的键使用默认值。
- 默认文件使用 UTF-8 BOM + CRLF，每个选项前有中文注释；键名保持 ASCII。
- 读取使用 ANSI profile API（`GetPrivateProfileIntA`）即可；BOM/中文注释不影响 `[Fix]` 段和 ASCII 键。
- 修改后重启游戏生效。

`[Fix]` 段的键与默认值：

| 键 | 默认 | 含义 |
|---|---:|---|
| `FramePacing` | 1 | QPC 毫秒时间源 |
| `CpuIdle` | 1 | 时间函数 1ms 节流 |
| `ClickLatch` | 1 | 短点击锁存 |
| `ClickRelease` | 1 | 抬起沿修复 |
| `SkipIntro` | 0 | 测试用：跳过片头/标题，直接进主菜单 |
| `Log` | 0 | 是否输出日志；0 = 不创建/不写入 |

默认 INI 内容（中文注释是规格的一部分）：

```ini
; <ASI同名>.ini -- 首次运行时自动生成。
; 1 = 启用，0 = 禁用。默认：除 SkipIntro 和 Log 外全部启用。
[Fix]
; 用 QPC 毫秒时间源替换 GetTickCount64，稳定 33ms 帧节奏
FramePacing=1
; 时间函数 1ms 节流，消除帧限速忙等（单核占用从约 100% 降到约 2%）
CpuIdle=1
; 短点击锁存：捕获 WM_LBUTTONDOWN/DBLCLK，在下一帧 GetDeviceState 注入一次按下
ClickLatch=1
; 抬起沿修复：绕过鼠标轮询的空闲快速路径，避免“只按下、不弹起”
ClickRelease=1
; 测试用：跳过片头动画和标题画面，直接进入主菜单（默认关闭）
SkipIntro=0
; 是否输出日志：0 = 不生成/不写入 <ASI同名>.log（默认），1 = 生成
Log=0
```

**日志**

- 日志路径：同目录、同基名、扩展名 `.log`。
- `Log=0`：**不要调用 `fopen`/创建/写入**，不能留下新的空日志文件；旧日志不要求删除。
- `Log=1`：以 `"w"` 截断打开并写入。
- 日志至少包含：
  - `InitializeASI exe=<basename> base=<ptr> asi=<path>`
  - `config: ini=<path> log=<path> FramePacing=... CpuIdle=... ClickLatch=... ClickRelease=... SkipIntro=... Log=... 60fps=...`（`60fps` 字段用于 Fast60 验收；顺序不限）
  - 各 Hook/补丁的状态（找到/未找到、成功/失败）。
- 不要在每个输入帧写日志；日志只用于启动/安装诊断。
- 验收构建可以额外输出 `latch` / `inject` 行（见 9.4），但发布构建必须遵守 `Log` 开关。

**验收**

- `tools/test_config.py`（第 9.7 节）：
  - 删除 INI/日志 → 启动 → INI 自动出现、键值匹配、无日志；
  - `Log=1` → 日志出现且包含生效配置；
  - `SkipIntro=1` → 日志出现三处 skip 补丁；
  - `Log=0` → 不创建日志。

### 2.6 SkipIntro（测试用，默认关闭）

- 仅 `SkipIntro=1` 时修改三处函数；`0` 时不得修改内存。
- 按函数序言签名定位，唯一命中才改写；找不到或命中不唯一时写日志并跳过该处，不要猜地址。
- 三处签名与改写：

| 功能 | 序言签名（十六进制） | 改写为 |
|---|---|---|
| 开场动画 | `88 4C 24 08 57 48 81 EC E0 01 00 00 48 8B FC B9 78 00 00 00` | `33 C0 C3`（`xor eax,eax; ret`） |
| `EVA.DK4` 片头动画（stream 9） | `40 56 57 48 81 EC 98 02 00 00 48 8B FC B9 A6 00 00 00` | `33 C0 C3` |
| 标题画面等待输入 | `40 56 57 48 81 EC B8 03 00 00 48 8B FC B9 EE 00 00 00` | `B8 01 00 00 00 C3`（`mov eax,1; ret`） |

- 效果：启动后约 1 秒直接进入主菜单，跳过开场画面。
- 仅测试用途；不修改游戏文件、存档或资源；删除 ASI 即恢复原启动流程。
- 默认 `SkipIntro=0`，正常游玩必须保持 0。

---

### 2.7 实验性 Fast60 + FPS 标题栏（`60fps=1`）

> 这一节是**可选实验功能**，默认关闭。它与 2.1 的 30Hz 时间修复是同一套 Hook，但目标不同：Fast60 让整个游戏时间轴约 **2× 速度**，并在标题栏显示真实 Present/s；它**不保持原时长**。

**支持范围**

- `DK4HD_tc.exe` / `DK4HD_kr.exe` / `DK4HD_jp.exe` / `DK4HD_sc.exe`，1.0.1 / 1.0.2。
- 不做 exe 名 gate；是否启用只看 `60fps=1` 和 Hook 是否全部成功。
- Fast60 不要求 `FramePacing=1`：`60fps=1` 且时间 Hook 成功时，Fast60 的时间源返回 2× QPC；`FramePacing=0` 只影响 Fast60 关闭时的 30Hz 路径（2.1 的原值返回）。不要在 Fast60 启用条件里额外要求 `FramePacing=1`。
- 时间函数仍用 2.3 的签名扫描；1.0.1 与 1.0.2 的时间函数 RVA 相同。鼠标轮询仍由签名扫描定位。

**配置**

| `60fps` | 行为 |
|---:|---|
| `0` | 原版 30Hz（默认） |
| `1` | 实验性 Fast60 + 标题栏 FPS；F10 可实时切换 Fast60/30Hz，不修改 INI |

- 默认 INI 注释必须写明“实验性”和“F10 可实时切换 Fast60/30Hz（不修改 INI）”。
- 不提供独立的 `FpsTitle` 键；标题栏随 `60fps=1` 一起启用。

**时间源**

- Hook `sub_1400D6630`（2.1 的同一函数）。
- 当 Fast60 开启时，对**所有调用者**返回 `2 * QPC 毫秒`，再按 2^32 取模；不做返回地址白名单。
- 不要再叠加任何帧数补偿：
  - ❌ 不把 UI 动画的 `a3` / total ×2；
  - ❌ 不把事件等待 `33×N` 的 N ×2；
  - ❌ 不把 `TimedWait` 的毫秒数 ×2；
  - ❌ 不把淡入淡出 `33×count` 的 count ×2；
  - ❌ 不需要转场回退 30Hz。
- 上述补偿属于“保持原时长”的实验模式；与 Fast60 的全局 ×2 叠加会变成 4× 或产生混合时钟。
- 结果：依赖时间源的等待/动画整体约 2× 速；1000ms 超时约变为 500ms。
- `CpuIdle=1` 时，Fast60 的时间调用必须使用**独立的 1ms 校准窗口**，不能复用 2.1 的“本次调用距上次 <1ms 就 Sleep”逻辑：
  - 维护 `thread_local` 的 `lastCalib`，与 30Hz 路径的 `last` **分开**；
  - 仅当 `lastCalib == 0 || now - lastCalib >= ticksPerMs` 时执行一次 `Sleep(1)`，重新读取 QPC，并更新 `lastCalib`；
  - 同一 1ms 窗口内的后续时间调用直接进入 ×2 返回，不再 Sleep；
  - 30Hz 路径（`g_fast60 == false`）继续使用 2.1 的 `last` 逻辑；两个模式不共用一个时间戳。
- 反例：在 Fast60 下沿用 2.1 的 `last` 判断并在每次调用都 `Sleep(1)`，会把主菜单/转场拖慢；实测特征是 `CpuIdle=1` 的 Fast60 主菜单 PresentMon p50 ≈ **19ms**（约 52fps），而 `CpuIdle=0` 或正确实现 ≈ **17ms**（约 58–60fps）。验收脚本会对该特征设硬门禁（9.9）。

```c
// Fast60 时间 Hook 的节流（伪代码；30Hz 分支仍按 2.1）
// now 已由 QueryPerformanceCounter 取得；每次 Sleep 后必须重新读取。
const bool fast = g_fast60.load(std::memory_order_relaxed);
static thread_local LARGE_INTEGER last = {};       // 30Hz 路径
static thread_local LARGE_INTEGER lastCalib = {};  // Fast60 路径，独立

if (!fast) {
    if (cpuIdle && last.QuadPart &&
        (now.QuadPart - last.QuadPart) < ticksPerMs) {
        Sleep(1);
        QueryPerformanceCounter(&now);
    }
    last = now;
} else if (cpuIdle &&
           (lastCalib.QuadPart == 0 ||
            (now.QuadPart - lastCalib.QuadPart) >= ticksPerMs)) {
    Sleep(1);
    QueryPerformanceCounter(&now);
    lastCalib = now;
}

uint64_t ms = (now.QuadPart * 1000ULL) / qpcFreq.QuadPart;
if (fast) ms *= 2;
return ms & 0xFFFFFFFFULL;
```

**DXGI Present**

- 通过临时 D3D11 device + swap chain 取 `IDXGISwapChain::Present` 的共享 vtable 槽位（slot 8）。
- 只统计/限速 `DXGI_PRESENT_TEST` 之外的 Present。
- Fast60 开启时，以 QPC 目标 `qpcFreq / 60` 限速；`last` 必须是 **thread_local**，避免多线程共用时间戳互相阻塞。
- 不要把 Present 复制两次（复制 Present 属于另一套 Safe60 行为，不在 Fast60 规格内）。
- Present Hook 失败时，Fast60 不得启用。

**FPS 标题栏**

- `EnumWindows` 找窗口类 `KT_DK4_PK_HD_WINDOW_NAME` 的本进程窗口；首次找到时保存原始标题。
- 独立监控线程每约 1 秒交换一次 Present 计数，按实际经过的 QPC 时间计算 FPS。
- 标题格式：

  ```text
  <原始标题> [<N> FPS | Fast60 (2x speed)]
  <原始标题> [<N> FPS | 30Hz]
  ```

- 用 `SetWindowTextA` 更新；不要每帧更新。
- F10 切到 30Hz 后标题仍保留 FPS 计数，只切换模式文字。

**F10 实时切换（必须防止失焦）**

- Hook `PeekMessageA`。当 `remove` 包含 `PM_REMOVE`，且消息是 `WM_KEYDOWN` / `WM_KEYUP` / `WM_SYSKEYDOWN` / `WM_SYSKEYUP` 且 `wParam == VK_F10` 时，丢弃该消息并继续取下一个消息。
- 游戏侧 `GetAsyncKeyState(VK_F10)` 必须返回 `0`。
- 监控线程不能走被抑制的 Hook；必须直接调用原始 `GetAsyncKeyState`（MinHook trampoline）读取物理键。
- F10 只在游戏窗口位于前台时切换 `g_fast60`，并写日志：

  ```text
  fast60 toggled: 1
  fast60 toggled: 0
  ```

- 只抑制 `GetAsyncKeyState` 而不抑制消息，或只 hook `PeekMessageW`，都不算完成：游戏导入的是 `PeekMessageA`，F10 会进入 MFC 菜单栏模式并抢走焦点。

**菜单按键去抖（方向键修复）**

- Fast60 下菜单循环更快，必须 Hook `GetAsyncKeyState` 做按键沿去抖。
- 只对以下离散菜单键生效：

  ```text
  VK_UP, VK_DOWN, VK_RETURN, VK_SPACE, VK_TAB, VK_ESCAPE
  ```

- 一次物理按下只放行一次；按住不足 400ms 时返回“未按下”；超过 400ms 后按 33ms 间隔重复。
- **绝不能把 `VK_LEFT` / `VK_RIGHT` 放进这个列表**：海战左右控帆是连续控制，去抖会让控帆一顿一顿。

**启用与日志**

Fast60 只有在以下全部成功时才启用：

- `60fps=1`；
- 时间函数 Hook 成功；
- DXGI Present Hook 成功；
- `GetAsyncKeyState` Hook 成功；
- `PeekMessageA` Hook 成功；
- `MH_EnableHook` 成功。

日志必须包含：

```text
fast60 key guard: 0
hook PeekMessageA: 0
dxgi present hook: 0
fast60 enabled at startup (global 2x clock, ~2x game speed)
```

失败时写：

```text
fast60 requested but not enabled (key=... peek=... present=... time=... enable=...)
```

**限制**

- 纯帧计数系统（RTTI sprite、事件 `a2` 循环等）不保证变成 60Hz；没有时间源调用的路径可能仍是 30Hz。
- 事件分支、战斗确定性、30 分钟长跑、Alt+Tab/全屏/分辨率切换未纳入本规格验收。
- 1.0.1 和 1.0.2 已验证功能；其他游戏更新若改变时间函数签名，必须重新扫描/补签名。

---

## 3. 鼠标轮询 Hook 的实现选择

两种实现都允许，行为必须一致：

### A. 手动 inline Hook

- 目标入口前 13 字节是固定的：

```text
48 89 5C 24 10 57 48 83 EC 30 48 8B D9
```

- 分配可执行 trampoline（RWX）：
  - 复制这 13 字节；
  - 追加 `mov rax, target+13; jmp rax`；
- 覆盖目标前 13 字节：
  - `mov rax, HookMousePoll; jmp rax; nop`（13 字节）；
  - `VirtualProtect` → 写入 → 恢复 → `FlushInstructionCache`。
- `HookMousePoll(obj)`：
  - 若尚未安装虚表 Hook，调用 `InstallDeviceVtable(obj)`；
  - 返回 `trampoline(obj)`。
- trampoline 是普通函数指针，调用原函数的 prologue 后跳回 `target+13`；原函数返回时回到 detour，detour 再返回给游戏调用方。

### B. MinHook

- 对扫描到的鼠标轮询地址 `MH_CreateHook`，detour 中做同样的 `InstallDeviceVtable`，然后调用 trampoline。
- MinHook 已经用于时间函数和 `PeekMessageA`，复用它也可以；但不要让 Hook 失败后继续执行未初始化的 trampoline。

**共同约束**

- `InstallDeviceVtable` 只执行一次；只对首次轮询对象 `+0x10` 的设备安装。
- **只改原始虚表的 `GetDeviceState` 槽位**；不要复制虚表、不要替换 `*(void***)dev`、不要改 this-adjustor。
- 保留原始 `GetDeviceState` 指针，供 Hook 调用。

---

## 4. 时间 / `PeekMessageA` Hook

- 时间函数：MinHook 或手动完整替换均可；detour **不调用**原时间函数。
- `PeekMessageA`：MinHook `MH_CreateHook(GetProcAddress(user32,"PeekMessageA"), ...)`；detour 调用原函数后检查消息。
- 所有 Hook 在 `MH_EnableHook(MH_ALL_HOOKS)` 前创建；时间 Hook 启用前确保 QPC 全局变量已初始化。
- Hook 失败要写日志并停止对应功能；不要继续使用空 trampoline。

---

## 5. 初始化顺序（必须一致）

`InitializeASI` 必须按以下顺序执行；日志顺序也一致：

1. `g_gameModule = GetModuleHandleA(nullptr)`；`g_base = (uintptr_t)g_gameModule`。
2. 取 ASI 自身模块（`GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS, &InitializeASI, ...)`），由 ASI 路径推导 INI / 日志路径（同目录、同基名）。
3. 读取 INI；不存在则创建默认文件；已存在则不覆盖；缺失键用默认值。
4. 仅当 `Log=1` 时打开日志（`"w"` 截断）；`Log=0` 时不得创建/打开/写入。
5. 写 `InitializeASI exe=<basename> base=<ptr> asi=<path>` 和 `config: ini=... log=... FramePacing=... CpuIdle=... ClickLatch=... ClickRelease=... SkipIntro=... Log=... 60fps=...`。
6. `QueryPerformanceFrequency`；`ticksPerMs = freq / 1000`（至少 1）；仅 `CpuIdle=1` 时 `timeBeginPeriod(1)`；写 `qpcFreq=... ticksPerMs=...`。
7. 读取 `.text` 的 `VirtualAddress` / `Misc.VirtualSize`。
8. 若 `SkipIntro=1`：按 2.6 定位并改写三处函数；写 `skip intro: ... patched at ...`（关闭时写 `skip intro: disabled`）。
9. 若 `FramePacing=1 || CpuIdle=1`：按 2.3 定位时间函数；若 `ClickLatch=1 || ClickRelease=1`：定位鼠标轮询；失败按第 1 节 RVA 回退并校验；写 `time=<ptr> mouse=<ptr> (scan)`。
10. 若 `ClickRelease=1`：校验并改写鼠标轮询 `+0x42`（`75 4E` → `EB 4E`）；写 `release fix: poll+0x42 patched at <ptr>`（关闭时写 `release fix: disabled`）。
11. 若 `ClickLatch=1`：安装鼠标轮询 inline Hook；写 `mouse poll hook: 1`（失败写 0，并停止后续输入 Hook）。
12. 若时间 Hook 或 `ClickLatch` 需要 MinHook：`MH_Initialize`；创建时间 Hook（若需要）；创建 `PeekMessageA` Hook（若 `ClickLatch=1`）；`MH_EnableHook(MH_ALL_HOOKS)`；分别写 `time hook: <status> (FramePacing=... CpuIdle=...)`、`hook PeekMessageA: <status>`、`MH_EnableHook: <status>`。
13. 首次鼠标轮询时安装 DInput 虚表 Hook；写 `hooked mouse GetDeviceState dev=<ptr> vt=<ptr> real=<ptr>`。

- 两个时间开关都为 0、且 `ClickLatch=0` 时，不要初始化 MinHook。
- `ClickRelease` 是纯内存字节补丁，不依赖 MinHook。

日志示例（`Log=1`，`SkipIntro=0`）：

```text
InitializeASI exe=DK4HD_tc.exe base=00007ff74cb00000 asi=...
config: ini=...\DK4HD_Fix.ini log=...\DK4HD_Fix.log FramePacing=1 CpuIdle=1 ClickLatch=1 ClickRelease=1 SkipIntro=0 Log=1 60fps=0
qpcFreq=10000000 ticksPerMs=10000
skip intro: disabled
time=00007ff74cbd6630 mouse=00007ff74cdb5b10 (scan)
release fix: poll+0x42 patched at 00007ff74cdb5b52
mouse poll hook installed at 00007ff74cdb5b10
mouse poll hook: 1
time hook: 0 (FramePacing=1 CpuIdle=1)
hook PeekMessageA: 0
MH_EnableHook: 0
hooked mouse GetDeviceState dev=... vt=... real=...
```

- 不要求日志文本逐字符相同，但关键字段和顺序应一致，便于验证。
- 不要在每个输入帧写日志。

---

## 6. 常量与结构

| 项目 | 值 |
|---|---|
| `DIMOUSESTATE` 大小 | 20 字节 |
| `lX/lY/lZ` 偏移 | `0/4/8` |
| `rgbButtons[0]` 偏移 | `12` |
| 左键位 | `0x80` |
| `IDirectInputDevice8::Poll` 虚表偏移 | `0xC8` |
| `IDirectInputDevice8::GetDeviceState` 虚表偏移 | `0x48` |
| 鼠标轮询对象设备指针偏移 | `+0x10` |
| 点击锁存有效期 | 1500ms |
| 时间节流阈值 | 1ms/线程；30Hz 用 `last`，Fast60 用独立 `lastCalib` |
| 主循环 tick | 33ms（0x21） |
| 鼠标轮询空闲快速路径跳转 | `+0x42`，`75 4E` → `EB 4E` |
| `ClickLatch` / `ClickRelease` 默认 | 1 / 1（必须成对开启） |
| `SkipIntro` 默认 | 0 |
| `Log` 默认 | 0 |
| INI / 日志路径 | ASI 同目录、同基名：`.ini` / `.log` |

`SkipIntro` 的三处签名与改写：

| 功能 | 序言签名 | 改写 |
|---|---|---|
| logos/title 动画 | `88 4C 24 08 57 48 81 EC E0 01 00 00 48 8B FC B9 78 00 00 00` | `33 C0 C3` |
| `EVA.DK4` 片头 | `40 56 57 48 81 EC 98 02 00 00 48 8B FC B9 A6 00 00 00` | `33 C0 C3` |
| 标题等待 | `40 56 57 48 81 EC B8 03 00 00 48 8B FC B9 EE 00 00 00` | `B8 01 00 00 00 C3` |

---

## 7. 构建

要求：Windows x64、MSYS2 MinGW-w64、MinHook 源码；验收脚本另需 Python 3.12+（见第 9 节）。

```bash
gcc -c -O2 -DNDEBUG -Iinclude -Isrc src/buffer.c -o buffer.o
gcc -c -O2 -DNDEBUG -Iinclude -Isrc src/hook.c -o hook.o
gcc -c -O2 -DNDEBUG -Iinclude -Isrc src/trampoline.c -o trampoline.o
gcc -c -O2 -DNDEBUG -Iinclude -Isrc src/hde/hde64.c -o hde64.o
ar rcs libminhook.a buffer.o hook.o trampoline.o hde64.o

g++ -shared -m64 -std=c++17 -O2 -DNDEBUG -Iinclude \
    -o DK4HD_Fix.asi DK4HD_Fix.cpp libminhook.a \
    -luser32 -lwinmm -ld3d11 -ldxgi -static-libgcc -static-libstdc++
```

- 链接 `user32`、`winmm`（`timeBeginPeriod`）、`d3d11`、`dxgi`（Fast60 Present Hook / FPS 标题栏）。
- 产物必须是 x64 PE DLL；用 `objdump -f` 检查 `pei-x86-64`。

---

## 8. 安装 / 卸载 / 加载器

游戏静态导入 `VERSION.dll`，所以代理 DLL 必须叫 `version.dll`；把 loader 改名成 `dinput8.dll` 会因为导出不匹配直接启动失败。

**`version.dll` 的确切来源**（不编译、不重写、不逆向 loader）：

1. 打开 Ultimate-ASI-Loader 官方发布页：`https://github.com/ThirteenAG/Ultimate-ASI-Loader/releases`
2. 注意坑：**版本化 release**（如 `v10.0.0`、`v9.7.4`）的 `Ultimate-ASI-Loader_x64.zip` 里**只有 `dinput8.dll`**，没有 `version.dll`。分代理的压缩包只挂在滚动预发布 tag `x64-latest` 下。
3. 直接下载这个资产：
   `https://github.com/ThirteenAG/Ultimate-ASI-Loader/releases/download/x64-latest/version-x64.zip`
   压缩包内是 `version.dll`（约 7MB，x64）与 `version-x64.SHA512`。
4. 先按包内 `.SHA512` 校验再复制：`certutil -hashfile version.dll SHA512`。滚动 tag 的内容会更新，校验不过就重新下载；不要用来源不明的同名 DLL。
5. 校验代理导出（应列出转发的版本函数）：

```bash
objdump -p version.dll | grep -E "GetFileVersionInfo(A|SizeA)"
```

- `DK4HD_Fix.asi` 与 `version.dll` 放在游戏根目录（与 `DK4HD_*.exe` 同级）。
- 首次启动自动生成同名 `DK4HD_Fix.ini`（UTF-8 + 中文注释）；默认除 `SkipIntro`、`Log` 外全部启用。
- `Log=0`（默认）时不生成/不写入 `DK4HD_Fix.log`；`Log=1` 时才生成同名日志。
- 保持系统 `DINPUT8.dll` 不变，不要让代理层介入 DirectInput COM 接口。
- 卸载：删除 `version.dll`、`DK4HD_Fix.asi`、`DK4HD_Fix.ini`、`DK4HD_Fix.log`（仅 `Log=1` 时存在）；游戏文件不需要恢复。
- 不要同时放多个 ASI Loader；与其他占用 `version.dll` 的 Mod 冲突。

---

## 9. 验收清单

本节由仓库 `tools/` 下的 Python 脚本执行（Python 3.12+ / Windows x64）。统一形式：

```bat
python tools\check_startup.py --game-dir "D:\Games\DK4HD"
```

`--game-dir` 是游戏根目录（与 `DK4HD_*.exe` 同级）。脚本只启动游戏、读 `<ASI同名>.log`、发点击、读 PresentMon CSV；不修改游戏文件，也不依赖任何反汇编产物。依赖见 `tools/requirements.txt`（`frida`、`psutil`、`Pillow`）。

> 默认 `Log=0` 不会生成日志。9.1 / 9.4 / 9.6 需要用日志或 Frida；9.1 / 9.4 会先备份 INI、临时把 `Log` 设为 1，结束后恢复。9.6 使用 Frida 直接读游戏输入状态，不依赖日志。

### 9.1 启动与 Hook —— `tools/check_startup.py`

```bat
python tools\check_startup.py --game-dir "D:\Games\DK4HD"
```

对 TC/KR/JP/SC 四个 exe 依次启动、等待 8s，逐项检查：进程仍存活、`InitializeASI exe=... base=...`、`config: ...`（键值与默认/当前 INI 一致）、`qpcFreq=... ticksPerMs=...`（`ticksPerMs >= 1`）、`skip intro: ...`、`time=... mouse=... (scan)`（或 `(rva)`）、`release fix: poll+0x42 patched at ...`、`mouse poll hook: 1`、`time hook: 0`、`hook PeekMessageA: 0`、`hooked mouse GetDeviceState dev=... vt=... real=...`。

- 脚本先备份 INI，临时把 `Log` 设为 1；结束后恢复原 INI 和日志。
- 每个 exe 输出 `[PASS]` / `[FAIL] : missing <项>`；
- 任一失败返回退出码 1；
- 全程不应出现错误框、崩溃或退出。

### 9.2 帧时间 —— `tools/measure_frames.py`

```bat
python tools\measure_frames.py --game-dir "D:\Games\DK4HD"
```

流程：启动游戏 → 等 18s（把加载/片头卡顿移出统计窗口）→ PresentMon `--v1_metrics --timed 15` → 统计 `msBetweenPresents`。未指定 `--presentmon` 时脚本自动把 Intel 官方 PresentMon 2.6.0 x64 下载到 `%TEMP%`。

通过条件（脚本内置同样的判定）：

- `SyncInterval` 唯一且为 `1`；
- p50 ∈ **32.0–34.5ms**（原版是 31.25/46.875 双档，p50 落 31.3 左右且大量 46–47ms 帧）；
- p95 ≤ **40ms**；
- 45–49.5ms 量化带帧占比 < 1%；
- p50 折算帧率 28–32fps（仍是约 30fps，不解锁 60fps）。

脚本打印 `frames / p5 / p50 / p95 / max / fps` 与判定行；失败时保留 CSV 路径便于复查。允许 30fps 附近，**不要**为了凑数值去改 33ms tick。

### 9.3 CPU —— `tools/measure_cpu.py`

```bat
python tools\measure_cpu.py --game-dir "D:\Games\DK4HD"
```

启动游戏、等 10s，用 `psutil` 按 1s 间隔采样每个线程的累计 CPU 时间并换算成峰值。百分比以“单核满载 = 100%”为单位，不需要再按核数换算。

- 通过：最热线程 ≤ **10% 单核**（未修复时该线程 ≈ 100% 单核，特征极明显）；
- 不要求所有线程都空闲；脚本只打印前 3 个线程。

### 9.4 点击 —— `tools/click_test.py`

自动验收需要一份**带诊断日志的验收构建**；发布构建必须关闭它：

- 编译宏 `-DDK4HD_TRACE_CLICKS`；
- 每次记录到 `WM_LBUTTONDOWN` / `WM_LBUTTONDBLCLK` 追加一行：`latch msg=0x0201 hwnd=...`；
- 每次真正把 `rgbButtons[0] |= 0x80` 注入时追加一行：`inject dev=0x... age=<ms>`（`age` = 锁存到注入的毫秒数）；
- 两个前缀必须可从行首解析；发布构建不得输出这两类行（遵守第 5 节“不要在每个输入帧写日志”）。

```bat
# 先构建验收版本，脚本会临时替换 .asi，跑完自动恢复原文件
python tools\click_test.py --game-dir "D:\Games\DK4HD" `
    --trace-asi build\DK4HD_Fix_trace.asi
```

脚本先备份 INI，临时把 `Log` 设为 1；把游戏窗口置前、光标移到客户区中心，按 5/10/20/40/80ms 各点 12 次（间隔 400ms），再按顺序配对 `latch` / `inject` 行：

- 每档 ≥ **10/12** 注入为通过（通常 12/12）；
- `duplicates` 必须为 **0**（没有 latch 对应的 inject = 重复按下沿）；
- `latches` 不得少于期望点击数（60）；
- 打印 `median age`，正常不超过 2 帧（0–47ms）。

不传 `--trace-asi` 时脚本只发点击，由人工在游戏里判断响应。
脚本结束后恢复原 INI；测试日志保留供复查（`Log=0` 的默认配置下正常启动不会留下日志）。

### 9.5 多语言

- `tools/check_startup.py` 的四个 exe 必须全部 `(scan)` 命中且 Hook 成功；
- `tools/click_test.py --exe DK4HD_sc.exe`（再换 `_kr` / `_jp`）每档 ≥ 10/12；
- `tools/click_release_test.py --exe DK4HD_sc.exe`（再换 `_kr` / `_jp`）按下/抬起数量相等；
- 回退一致性：用一份临时跳过签名扫描的验收构建（例如加宏把扫描短路），`time=` / `mouse=` 地址必须与**对应版本**的签名扫描结果完全一致（1.0.1 用 `0x2B5B10` 等，1.0.2 用 `0x2B5BC0` 等）；不一致说明第 1 节 RVA 表或第 2.3 节前缀校验有误。

### 9.6 抬起沿 —— `tools/click_release_test.py`

```bat
pip install -r tools\requirements.txt
python tools\click_release_test.py --game-dir "D:\Games\DK4HD" --exe DK4HD_tc.exe --hold-ms 5 --repeats 10
```

脚本启动游戏、置前，用 Frida 统计按键边沿函数（TC/JP/SC `0x41BB0`，KR `0x41D20`）的返回值：`3` = 按下，`1` = 抬起；只统计左键。

- 通过：`presses == releases == repeats`（默认 10/10）。
- 对照：把 INI 改为 `ClickLatch=1`、`ClickRelease=0`，重启后再跑；应看到 `releases` 明显少于 `presses`（证明 `ClickRelease` 开关生效）。
- 脚本不修改 INI/日志；需要 Frida 17+。

### 9.7 配置 / 日志 / 片头 —— `tools/test_config.py`

```bat
python tools\test_config.py --game-dir "D:\Games\DK4HD"
```

脚本分阶段执行并**备份/恢复原 INI 与日志**：

1. 删除 INI/日志 → 启动 → 断言 INI 自动生成；`[Fix]` 六个键存在且默认值为 `1/1/1/1/0/0`；日志不存在。
2. 写入 `Log=1`、`SkipIntro=1`（其余 1）→ 启动 3s → 断言日志存在、`config:` 含 `SkipIntro=1`、`skip intro:` 至少三行 patched；窗口出现且进程存活。
3. 写入 `Log=0`、删除日志 → 启动 2s → 断言日志不存在。
4. 结束测试进程，恢复原 INI/日志。

- 退出码 1 表示任一断言失败；失败时保留测试日志/INI。
- 该脚本只验证配置/日志/片头补丁的安装，不判断画面内容；菜单截图由 9.8 的可选人工步骤或 `tools/test_config.py --screenshot` 输出。

### 9.8 人工画面确认（可选）

在 `SkipIntro=1` 下启动游戏，约 1 秒内应直接出现主菜单（继续 / 新游戏 / 结束游戏），不出现开场动画或 `EVA.DK4` 片头。用 `Win+Shift+S` 或 `python tools\test_config.py --screenshot` 留档即可。

---


### 9.9 实验性 Fast60 —— `tools/fast60_test.py`

```bat
python tools\fast60_test.py --game-dir "D:\Games\DK4HD"
```

脚本默认依次测试 TC/KR/JP/SC；也可以：

```bat
python tools\fast60_test.py --game-dir "D:\Games\DK4HD" --exe DK4HD_kr.exe
python tools\fast60_test.py --game-dir "D:\Games\DK4HD" --warmup 12 --presentmon-seconds 15
```

脚本会先备份 INI/日志，临时写入 `[Fix]`：`FramePacing=1`、`CpuIdle=1`、`ClickLatch=1`、`ClickRelease=1`、`SkipIntro=1`、`Log=1`、`60fps=1`，跑完恢复原文件。`SkipIntro=1` 确保采样落在主菜单而不是片头。

对每个 exe 检查：

- 日志含 `CpuIdle=1`、`60fps=1`、`fast60 enabled at startup`、`dxgi present hook: 0`、`fast60 key guard: 0`、`hook PeekMessageA: 0`；
- 标题栏出现 `Fast60 (2x speed)` 且能解析出 FPS；
- F10 后标题变为 `30Hz`，再按恢复 `Fast60 (2x speed)`；
- 两次 F10 前后 `GetForegroundWindow()` 都是游戏窗口。

PresentMon（默认启用；`--no-presentmon` 仅做功能检查）在第一个选中的 exe（默认 TC）上采样 Fast60 主菜单稳态：

- `p50 <= 18.0ms`、`p95 <= 20.0ms`、`fps >= 55`；正确实现通常 p50 ≈ **17.0ms**、p95 ≈ **18.5–19.0ms**、fps ≈ 58–60；
- 该门禁专门捕捉“Fast60 每次时间调用都 Sleep(1)”的回归：错误实现 p50 ≈ **19.2ms**、p95 ≈ **20.6ms**、fps ≈ 52，会判 FAIL；
- 其余语言只记录 p50/p95，不按语言间差异设门槛；30Hz 时 p50 约 33–38ms 仅作记录。

通过标准：

- 上述功能检查在 **TC/KR/JP/SC 四语言** 全部通过；
- 1.0.1 与 1.0.2 的功能检查一致（时间函数/鼠标轮询签名扫描命中，不依赖固定版本号）；
- 第一个选中 exe 的 PresentMon 性能门禁通过；`--no-presentmon` 只用于排查功能，不能作为最终验收；
- `warmup` 默认 8 秒，建议至少 8 秒；采样时必须已经在主菜单（测试 INI 的 `SkipIntro=1`）；
- 任一 Hook 失败、标题不切换、F10 后失焦、启动崩溃、第一个选中 exe 的 PresentMon 采样失败或性能门禁不达标 → 失败。

> 本脚本是 Fast60 的权威验收版本；主项目 `test/fast60_test.py` 已同步相同的临时 INI 与 `p50/p95/fps` 性能门禁。`--presentmon` 未指定时会自动下载 Intel PresentMon 2.6.0。

---

## 10. 常见错误（不要这样做）

- ❌ Hook `DirectInput8Create` 并包装返回的 `IDirectInput8` / `CreateDevice`：容易破坏 DInput 初始化。
- ❌ 复制 DInput 虚表并替换设备 vptr：`Acquire`/`SetCooperativeLevel` 可能返回 `E_INVALIDARG`；失焦后鼠标无法恢复。**只原地改 `GetDeviceState` 槽位。**
- ❌ 用 18 字节 RTC 前缀搜索时间函数：会命中 2000+ 次；必须用 `div 100000000h` 唯一签名。
- ❌ 只硬编码 TC RVA：KR/JP/SC 会失效；必须扫描 + 回退表 + 校验。
- ❌ 在时间 Hook 里对所有调用 `Sleep(1)` 而不做 1ms 阈值：会影响正常计时；必须每线程判断间隔。
- ❌ 用 `DIPROP_BUFFERSIZE` / `GetDeviceData`：本设计不依赖缓冲；会引入额外状态问题。
- ❌ 用 `SendInput` / 鼠标移动触发 UI：点击锁存只改 `GetDeviceState` 返回值。
- ❌ 在 `DllMain` 里创建线程、加载 DLL、初始化 MinHook：放在 `InitializeASI` 中。
- ❌ 注入后不清 pending：会导致重复按下；`exchange(false)` 后无论是否过期都要清除。
- ❌ 对非鼠标设备注入 `0x80`：会污染键盘/手柄状态；必须过滤 `self == g_mouseDevice`。
- ❌ 只开 `ClickLatch` 而不开 `ClickRelease`：锁存只补“按下”，可能让左键卡在按下；默认必须两者都开。
- ❌ 不校验 `75 4E` 就直接往鼠标轮询 `+0x42` 写 `EB 4E`：版本/签名不符时会改错代码；必须先扫描并校验。
- ❌ `Log=0` 时仍 `fopen` 日志：会在游戏目录留下空日志；关闭时不要创建/打开/写入。
- ❌ 硬编码 `DK4HD_Fix.ini` / `.log`：ASI 改名后应使用同基名；必须从 ASI 自身路径推导。
- ❌ 覆盖已存在的 INI：用户配置会被重置；只在文件不存在时创建默认值。
- ❌ 把 `SkipIntro=1` 当正式功能或默认开启：它只是测试辅助，正常游玩应保持 0。
- ❌ 用 UTF-16 INI 配 `GetPrivateProfileIntA`：键会读不到；默认文件用 UTF-8（BOM）+ ASCII 键。
- ❌ 自己写 / 逆向一个 `version.dll` ASI loader 或手写其转发导出：本文档不描述 loader，交付物只有 `.asi`；loader 用官方预编译二进制。
- ❌ 修改游戏 exe / 存档 / 资源：该模块只通过 Loader + ASI 运行。

---

- ❌ 在 Fast60 全局 ×2 的同时仍安装 UI `a3×2`、事件等待帧数×2、TimedWait 毫秒×2、淡入淡出 count×2：会双重补偿，得到 4× 或混合时钟。Fast60 不做这些补偿。
- ❌ 把 `VK_LEFT` / `VK_RIGHT` 放进菜单按键去抖：海战左右控帆会变成一格一格，必须排除。
- ❌ 只在 `GetAsyncKeyState` 里屏蔽 F10，却让监控线程也走同一个 Hook：监控永远读不到 F10，无法切换。
- ❌ 只 hook `PeekMessageW`：游戏导入的是 `PeekMessageA`，F10 仍会进入 MFC 菜单栏并失焦。
- ❌ Fast60 直接复用 2.1 的 `last` 阈值并在每次时间调用都 `Sleep(1)`：主菜单 p50 会从 ~17ms 掉到 ~19ms（~52fps），`tools/fast60_test.py` 的 `p50 <= 18ms` 门禁会判 FAIL；必须用独立 `lastCalib` 做 1ms 窗口限速。
- ❌ 30Hz 与 Fast60 共用同一个 `last` 时间戳：切换模式后首个调用会误 Sleep，且无法实现 Fast60 的 1ms 窗口；必须分开 `last` / `lastCalib`。
- ❌ 在 Fast60 的时间 Hook 里仍按返回地址白名单 ×2：非白名单系统不会加速，形成混合速度；Fast60 必须全局 ×2。
- ❌ 在 Present Hook 里对 `DXGI_PRESENT_TEST` 也限速/计数：会污染 FPS 并引入额外等待。
- ❌ 标题栏每帧 `SetWindowTextA`：会造成额外开销；最多每秒一次。
- ❌ 任一 Hook 失败仍部分启用 Fast60：必须全有或全无，并写 `fast60 requested but not enabled (...)`。

## 11. 第三方组件与许可证

- Ultimate-ASI-Loader：ThirteenAG，MIT；`version.dll` 代理。
- MinHook：TsudaKageyu，BSD 2-Clause。
- PresentMon：Intel，测试工具；遵循其许可证。
- 游戏本体、存档、素材版权归 Koei Tecmo；本文档与任何实现都不包含它们。

> 独立实现时，建议先做签名扫描 + QPC 时间 Hook，确认四个 exe 都能扫描/启动；再加入鼠标 Hook、点击锁存和 `ClickRelease` 抬起沿修复；然后加同名 INI/日志与 `SkipIntro`（默认关）；最后实现 `60fps=1` 的实验性 Fast60 + 标题栏/F10/方向键修复，按第 9 节验收。Fast60 必须默认关闭，且不要与“保持原时长”的补偿 Hook 混用。
