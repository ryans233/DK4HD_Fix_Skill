# DK4HD Runtime Compatibility Skill

一份面向 LLM 的独立实现规格（skill）：装上后消除 DK4HD 的帧节奏抖动、单核满载和短点击丢失，而不改游戏本体。

**English:** A standalone behavioral specification/skill for an LLM to implement a DK4HD runtime compatibility module from scratch — fixing frame-pacing jitter, one-core CPU spin, and dropped short clicks without touching the game itself.

## 这个 Skill 是什么

先看装上之后玩家能感知到什么。原版 DK4HD 在多数机器上有三个毛病，这个模块只修这三个：

| 你会遇到 | 原版 | 装上后 | 为什么 |
|---|---|---|---|
| 画面一顿一顿，像卡在不均匀的节拍上 | 帧间隔在 31.25ms / 46.875ms 两档间跳 | p50 ≈ 33.0–33.1ms，p95 ≈ 35.5–35.8ms | 帧限速用的 `GetTickCount64` 只有 15.625ms 粒度，33ms 的节拍被量化成两档 |
| 风扇狂转，一个核心被吃满 | 最热线程 ≈ 99.7% 单核 | ≈ 1.5–2.3% | 帧限速在对时间函数自旋忙等 |
| 快速点一下没反应，必须按久一点 | 5ms 点击约 2/12 生效 | 11–12/12 生效 | 鼠标每帧才采样一次（约 31–33ms），5–20ms 的点击整段落在两次采样之间 |

除此之外画面、存档、玩法都不变：没有新窗口、新菜单、新按键，游戏仍是约 30fps，也不解锁 60fps。

`skills.md` 是写给 LLM 的实现规格，把上面三种修复拆成**可验证的行为规格**：Hook 哪个函数、用哪段字节签名定位、虚表改哪个槽位、常量是多少、初始化顺序、日志长什么样、每项怎么验收。照着做完就产出一个 x64 ASI 插件。它不是设计综述也不是教程，而是“做到什么程度才算完成”的判据。

文档只包含接口事实、常量、签名和行为规格，不包含游戏代码、资源或内部实现细节。

## 如何使用

1. 将 `skills.md` 作为上下文提供给 LLM（或作为 skill/system prompt 附件）。
2. 明确要求它**只根据规格独立实现**，不要复制任何现有实现。
3. 指定目标环境：
   - Windows x64
   - 游戏 `DK4HD_tc.exe` / `DK4HD_kr.exe` / `DK4HD_jp.exe` / `DK4HD_sc.exe`
   - MSYS2 MinGW-w64 + Ultimate-ASI-Loader + MinHook
4. 让 LLM 按文档顺序实现：
   - 先做时间函数扫描 + QPC Hook，验证四个 exe 可启动；
   - 再加鼠标轮询 Hook 和 `GetDeviceState` 点击锁存；
   - 最后跑文档中的验收清单。
5. 构建产物放到游戏根目录：
   ```text
   version.dll
   DK4HD_InputFix.asi
   ```
   启动游戏，检查 `DK4HD_InputFix.log`。

## 适用边界

- 单机游戏互操作性修复；不绕过 DRM、不修改游戏文件、不包含游戏资源。
- 游戏仍约 30 FPS；该规格不包含 60 FPS 解锁。
- 连续点击偶发“按下未弹起”属于另一个独立问题，不在本规格范围内。
- 对游戏进行互操作性修改可能受 EULA、当地法律或平台条款限制；使用前请自行确认合法性。

## 文件

```text
README.md      # 本说明
skills.md      # 完整行为规格 / skill
```

第三方组件（Ultimate-ASI-Loader、MinHook、PresentMon）遵循各自许可证。
