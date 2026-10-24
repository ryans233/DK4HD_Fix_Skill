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
DK4HD_InputFix.asi # 独立实现的 x64 DLL（名称可自定；下同）
```

- `version.dll` **不是**实现目标。直接取 Ultimate-ASI-Loader 官方 release 的 x64 `version.dll` 复制到游戏根目录；不需要自己编译、重写或逆向 loader。
- **不要**自行实现 ASI loader / `version.dll` 代理：本文档未提供导出转发、ASI 目录扫描、加载时序等规格，loader 因此不可按本文档验收，也不在独立实现范围内。
- 若环境里已有其他**预编译** ASI loader（其他代理 DLL 名），可直接复用；这属于部署选择，不是实现任务。
- 成功判定只看 `DK4HD_InputFix.asi` 的行为（第 2 节 + 第 9 节）；loader 只需满足“游戏能加载它、它能发现并加载 `.asi`”这一既有事实。

安装：两个文件放在游戏根目录（与 `DK4HD_tc.exe` 同级）。插件导出：

```c
extern "C" __declspec(dllexport) void InitializeASI(void);
```

`DllMain(DLL_PROCESS_ATTACH)` 只做 `DisableThreadLibraryCalls`，不要在 `DllMain` 里做重活。

支持的 exe：

| exe | 时间函数 RVA | 鼠标轮询 RVA |
|---|---|---|
| `DK4HD_tc.exe` | `0xD6630` | `0x2B5BC0` |
| `DK4HD_kr.exe` | `0xD8B10` | `0x2BD8A0` |
| `DK4HD_jp.exe` | `0xD6330` | `0x2B58E0` |
| `DK4HD_sc.exe` | `0xD62F0` | `0x2B5880` |

> RVA 基于 ImageBase `0x140000000`；运行时地址 = exe 模块基址 + RVA。优先扫描签名，RVA 表仅作回退。表内值为函数入口，回退时仍必须通过第 2.3 节的前缀校验；四条 RVA 必须等于签名扫描得到的地址（第 9.5 节）。

---

## 2. 必须实现的三个行为

### 2.1 帧节奏：QPC 时间源 + 1ms 节流

**原始行为（需要替换）**

- 主循环使用 `GetTickCount64()` 的低 32 位作为毫秒时间源。
- `GetTickCount64` 默认粒度约 15.625ms；33ms 帧限速会被量化为 31.25ms / 46.875ms 两档，产生 31/47ms 抖动。
- 帧限速对时间函数存在高频自旋，导致单核接近 100%。

**目标行为**

- Hook 时间函数，**完全替换返回值**为 QPC 毫秒值，不调用原函数。
- 每线程维护 `lastQpc`：同一线程两次调用间隔 < 1ms 时 `Sleep(1)`，然后重新读取 QPC。
- `InitializeASI` 中调用 `timeBeginPeriod(1)`。
- 返回 `uint32_t`（调用方使用低 32 位），QPC 毫秒值按 2^32 取模。

伪代码：

```c
uint64_t HookTime(void* unused) {
    LARGE_INTEGER now;
    QueryPerformanceCounter(&now);

    static thread_local LARGE_INTEGER last = {};
    if (last.QuadPart && (now.QuadPart - last.QuadPart) < ticksPerMs) {
        Sleep(1);
        QueryPerformanceCounter(&now);
    }
    last = now;

    uint64_t ms = (now.QuadPart * 1000ULL) / qpcFreq.QuadPart;
    return ms & 0xFFFFFFFFULL;
}
```

- `ticksPerMs = qpcFreq / 1000`；若为 0 则设为 1。
- **顺序要求**：QPC 频率、`ticksPerMs`、`timeBeginPeriod(1)` 必须在启用 Hook 前设置好，否则 Hook 可能除以 0。
- 该 Hook 必须只替换时间函数；不要修改 `GetTickCount64` 本身。

**验收**

- PresentMon 15s：`msBetweenPresents` p50 ≈ 33.0–33.1ms，p95 ≈ 35.5–35.8ms，`SyncInterval=1`。
- 最热线程 CPU ≈ 1.5–2.3% 单核（原版约 99.7%）。
- 游戏仍约 30fps；**不要**尝试改 33ms tick 解锁 60fps。

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

- 注入只持续**一次** `GetDeviceState`；下一帧返回真实状态，游戏得到完整“按下沿 → 抬起沿”。
- 不开启 `DIPROP_BUFFERSIZE`，不调用 `GetDeviceData`。
- 不注入鼠标移动、不发送 `SendInput`、不修改光标位置。
- 不对键盘/手柄设备注入；只对首次捕获的鼠标设备。

**验收**

- 5/10/20/40/80ms 点击各 12 次，通常每档 12/12；至少 5ms 点击从约 2/12 提升到 11–12/12。
- 不出现重复按下沿（12 次点击不应变成 20+ 次）。
- 连续点击偶发“按下未弹起”属于另一个独立问题，不在本模块范围内；不要用注入鼠标移动去处理它。

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

`InitializeASI` 必须按以下顺序执行，日志顺序也一致：

1. `g_gameModule = GetModuleHandleA(nullptr)`；`g_base = (uintptr_t)g_gameModule`。
2. 打开 `DK4HD_InputFix.log`（exe 同目录，`"w"` 截断）。
3. 写 `InitializeASI exe=<basename> base=<ptr>`。
4. `QueryPerformanceFrequency`；`ticksPerMs = freq / 1000`（至少 1）；`timeBeginPeriod(1)`；写 `qpcFreq=... ticksPerMs=...`。
5. 扫描 `.text` 定位时间函数和鼠标轮询；失败按第 1 节 RVA 回退并校验。
6. 写 `time=<ptr> mouse=<ptr> (scan)`。
7. 安装鼠标轮询 Hook；写 `mouse poll hook: 1`（失败写 0，并停止后续输入 Hook）。
8. `MH_Initialize`；写失败返回。
9. 创建时间 Hook；写 `hook time: <status>`。
10. 创建 `PeekMessageA` Hook；写 `hook PeekMessageA: <status>`。
11. `MH_EnableHook(MH_ALL_HOOKS)`；写 `MH_EnableHook: <status>`。
12. 首次鼠标轮询时安装 DInput 虚表 Hook；写 `hooked mouse GetDeviceState dev=<ptr> vt=<ptr> real=<ptr>`。

日志示例：

```text
InitializeASI exe=DK4HD_tc.exe base=00007ff74cb00000
qpcFreq=10000000 ticksPerMs=10000
time=00007ff74cbd6630 mouse=00007ff74cdb5b10 (scan)
mouse poll hook installed at 00007ff74cdb5b10
mouse poll hook: 1
hook time: 0
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
| 时间节流阈值 | 1ms/线程 |
| 主循环 tick | 33ms（0x21） |

---

## 7. 构建

要求：Windows x64、MSYS2 MinGW-w64、MinHook 源码；

```bash
gcc -c -O2 -DNDEBUG -Iinclude -Isrc src/buffer.c -o buffer.o
gcc -c -O2 -DNDEBUG -Iinclude -Isrc src/hook.c -o hook.o
gcc -c -O2 -DNDEBUG -Iinclude -Isrc src/trampoline.c -o trampoline.o
gcc -c -O2 -DNDEBUG -Iinclude -Isrc src/hde/hde64.c -o hde64.o
ar rcs libminhook.a buffer.o hook.o trampoline.o hde64.o

g++ -shared -m64 -std=c++17 -O2 -DNDEBUG -Iinclude \
    -o DK4HD_InputFix.asi DK4HD_InputFix.cpp libminhook.a \
    -luser32 -lwinmm -static-libgcc -static-libstdc++
```

- 链接 `user32`、`winmm`（`timeBeginPeriod`）。
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

- `DK4HD_InputFix.asi` 与 `version.dll` 放在游戏根目录（与 `DK4HD_*.exe` 同级）。
- 保持系统 `DINPUT8.dll` 不变，不要让代理层介入 DirectInput COM 接口。
- 卸载：删除 `version.dll`、`DK4HD_InputFix.asi`、`DK4HD_InputFix.log`；游戏文件不需要恢复。
- 不要同时放多个 ASI Loader；与其他占用 `version.dll` 的 Mod 冲突。

---

## 9. 验收清单

### 9.1 启动与 Hook

对 TC/KR/JP/SC 四个 exe 分别：

- 游戏窗口出现，进程存活；
- `DK4HD_InputFix.log` 有 `time=... mouse=... (scan)`（或回退日志）、`mouse poll hook: 1`、`hook time: 0`、`hooked mouse GetDeviceState`；
- 无错误框、无崩溃。

### 9.2 帧时间

PresentMon 15s，前台，`--v1_metrics`：

- `msBetweenPresents` p50 ≈ 33.0–33.1ms；
- p95 ≈ 35.5–35.8ms；
- `SyncInterval=1`；
- 游戏仍约 30fps。

### 9.3 CPU

- 最热线程 ≈ 1.5–2.3% 单核（原版 99.7%）。
- 不要求所有线程都空闲。

### 9.4 点击

- 5/10/20/40/80ms 各 12 次：通常 12/12；5ms 不得低于 10/12。
- 无重复按下沿。
- 不注入鼠标移动、不改变光标位置。

### 9.5 多语言

- 四个 exe 的签名扫描都命中；
- 5ms 点击各至少 10/12；
- 回退 RVA 与扫描结果一致。

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
- ❌ 自己写 / 逆向一个 `version.dll` ASI loader 或手写其转发导出：本文档不描述 loader，交付物只有 `.asi`；loader 用官方预编译二进制。
- ❌ 修改游戏 exe / 存档 / 资源：该模块只通过 Loader + ASI 运行。

---

## 11. 第三方组件与许可证

- Ultimate-ASI-Loader：ThirteenAG，MIT；`version.dll` 代理。
- MinHook：TsudaKageyu，BSD 2-Clause。
- PresentMon：Intel，测试工具；遵循其许可证。
- 游戏本体、存档、素材版权归 Koei Tecmo；本文档与任何实现都不包含它们。

> 独立实现时，建议先写一个最小验证 DLL：只 Hook 时间函数并打印日志，确认四个 exe 都能扫描/启动；再加入鼠标 Hook 和点击锁存；最后跑验收清单。不要一次性修改全部路径。
