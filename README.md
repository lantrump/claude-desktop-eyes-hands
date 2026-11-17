# desktop-eyes-hands

Claude Code 的"手和眼"skill：先截屏给模型看，再用真实的鼠标键盘操作 Windows 桌面。

- **眼睛**：`pc.py shot` 用 GDI 抓屏存 PNG（全屏 / 区域 / 窗口），模型直接读图。
- **手**：`click / type / key / drag / scroll / activate` 走 SendInput，复用语音助手项目里已验证的 `desktop_ctl.py`（DPI 感知、提权窗口拦截）。
- **鼠标守卫**：键鼠动作前自动武装；占用期间物理鼠标完全没反应，用户以此判断"AI 正在接管"，租约到期自动恢复。
- **停止键**：占用期间 Ctrl+C 立刻关闭操控、把终端弹回最前，**不打断** Claude Code 当前任务。
- **批处理**：`batch` 一次进程连跑多条命令，省掉每步的启动和往返。

## 前置

- Windows 10/11、Python 3
- 依赖 [dsh-voice-screen-assistant](https://github.com/lantrump/dsh-voice-screen-assistant) 里的 `desktop_ctl.py`。
  项目不在默认位置时，用环境变量 `DSH_VOICE_HOME` 指向它（默认 `D:\dsh工作区\语音屏幕助手`）。

## 安装

把本目录放到 `%USERPROFILE%\.claude\skills\desktop-eyes-hands\`，Claude Code 会自动热加载。

```bash
PY="F:/miniconda3/python.exe"          # 本机 python 可能是商店占位符
PC="$USERPROFILE/.claude/skills/desktop-eyes-hands/pc.py"
```

## 核心循环

```
shot 截图 → Read 读图 → 判断 → 动作 → shot 复核
```

## 常用命令

```bash
$PY $PC shot                                  # 全屏，输出 PNG 路径 + 前景窗口 + 光标
$PY $PC shot --region 100,100,800,600 --scale 2
$PY $PC windows                               # 按 Z 序列出可见窗口
$PY $PC activate RobotStudio                  # 已开则切过去
$PY $PC click 800 450 [--button right] [--double]
$PY $PC key ctrl+s
$PY $PC type "文本" [--mode paste|line] [--clear]
$PY $PC batch '[["click","800","450"],["wait","300"],["type","文本","--mode","paste"]]'
$PY $PC guard status                          # 守卫状态（占用中/已停止）
$PY $PC guard off                             # 立即释放
$PY $PC home                                  # 释放 + 终端回前台，收工用
```

输出恒为一行 JSON；`{"ok":false,"code":"elevated"}` 表示目标窗口是管理员权限；
`{"ok":false,"code":"stopped"}` 表示用户按了停止键，操控已关闭，需 `guard on` 才能恢复。

## 安全规则

1. 先看再点，不盲点坐标。
2. 输入前确认前景窗口是目标程序。
3. 遇到 `elevated` 立即停下，不要重试。
4. 密码、支付、删除确认类界面先问用户。
5. 截图存 `%TEMP%\claude-eyes\`，任务结束删除。

完整规则和已知限制见 [SKILL.md](SKILL.md)。

## 已知限制

- 3D 视口 / 画布类区域读不出精确数值，只能读界面上显示的数字。
- 用户移动鼠标会打断注入的点击序列（鼠标守卫已缓解）。
- 每次调用是新进程（约 0.14s），已知序列用 `batch`。
- VS Code 内置终端不会被自动最小化；管理员权限窗口（UIPI）下钩子可能收不到事件。
