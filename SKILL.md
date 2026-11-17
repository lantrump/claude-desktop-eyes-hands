---
name: desktop-eyes-hands
description: 看屏幕与操作本机 Windows 桌面：截屏（全屏/区域/窗口）后用多模态读图，并用真实鼠标键盘执行点击/输入/快捷键/拖拽/滚轮/切换窗口。当任务需要操作 GUI 程序（如 RobotStudio、浏览器、办公软件）、查看屏幕上有什么、或把内容填进别的程序时使用。不用于纯代码任务。
---

# 桌面手眼

眼睛 = `pc.py shot` 抓屏存 PNG，回复里直接用 Read 打开读图。
手 = `pc.py click/type/key/...`，底层复用语音助手项目已验证的 `desktop_ctl.py`（SendInput、DPI 感知、提权窗口拦截）。

**大脑是当前会话的模型本身**，不调用语音助手的 DeepSeek 视觉模型，也不依赖它是否在运行。

## 前置

- 仅 Windows 10/11。
- Python 3。本机 `python` 可能是微软商店占位符（敲了没反应），用 `F:/miniconda3/python.exe`。
- 动作层来自 `D:\dsh工作区\语音屏幕助手\desktop_ctl.py`；项目挪过位置就设环境变量 `DSH_VOICE_HOME` 指过去。

命令模板（下文用 `$PY` `$PC` 代替）：

```bash
PY="F:/miniconda3/python.exe"
PC="C:/Users/96225/.claude/skills/desktop-eyes-hands/pc.py"
```

## 核心循环

```
shot 截图 → Read 读图 → 判断 → 动作 → shot 复核
```

坐标体系：`shot` 截的是**虚拟屏幕**，输出里的 `x/y/w/h` 是截图左上角在虚拟屏幕上的物理像素坐标（多显示器时可能为负）。点击坐标 = 截图内的像素坐标 + 截图偏移。

读小字：先 `--region` 裁出目标区域，再 `--scale 2`（或 3）放大，比整屏截图清楚。

## 安全规则（必须遵守）

1. **先看再点。** 每一次点击前必须有当前截图依据，不许盲点坐标。
2. **输入前确认前景窗口。** `shot` 输出自带 `foreground`（app/title/hwnd），打字/快捷键前确认它是目标程序；不是就先 `activate` 或点击目标窗口。
3. **批量动作后截图取证。** 一轮操作结束、以及任何不可逆操作之前，都要截图。
4. **elevated 就停。** 返回 `{"ok":false,"code":"elevated"}` 表示目标窗口是管理员权限，系统会静默丢弃输入——停下并告诉用户，不要重试。
5. **敏感界面请示。** 密码、支付、删除确认、系统设置类页面，停下来问用户，不自己点。
6. **截图是临时文件。** 默认存 `%TEMP%\claude-eyes\`，任务结束时删除，不要把用户屏幕内容留在别处。
7. **告知用户。** 开始注入键鼠前说明要做什么；操作期间提醒用户不要动鼠标，否则落点会偏。
8. **系统截图快捷键会被拒。** `key printscreen`、`win+shift+s` 之类返回错误，这是故意的（会弹出截图工具盖住屏幕）。

## 鼠标守卫（自动）

任何键鼠动作（click/type/key/move/activate/...）执行前会**自动武装守卫**：独立进程 `guard.py` 在租约期内吞掉**物理**鼠标事件（移动/点击/滚轮），只放行 SendInput 注入事件。效果是用户在你动作期间动鼠标没有任何反应——这是"AI 正在接管"的信号。

- 租约默认 20 秒，每个动作自动续期；动作停下 20 秒后鼠标恢复。
- 守卫进程在租约过期 120 秒后自己退出（钩子卸载 = 立刻恢复），崩溃也一样，失败安全。
- 键盘基本不碰：Esc / Ctrl+Alt+Del 始终有效；唯一例外是 **Ctrl+C 停止键**（见下）。
- 按钮抬起事件永远放行，避免目标程序认为按键卡住。
- 手动控制：`guard on --ttl 60` 强制占用、`guard off` 立即释放、`guard status` 查看。
- 限制：管理员权限窗口（UIPI）下钩子可能收不到事件；Raw Input 游戏可能绕过。
- 武装会真的冻结用户鼠标：多步操作前先告知用户。

### 停止键与终端最小化

- 守卫占用期间按 **Ctrl+C** = 关闭操控：立刻交还鼠标、终端弹回最前、写停止标记。**不往终端注入 Ctrl+C**，所以不会打断 Claude Code 的任务；表现是之后每个键鼠动作都返回 `{"ok":false,"code":"stopped"}`——看到就停手、告诉用户，用户确认后再 `guard on` 恢复。代价是租约期内任何程序里的 Ctrl+C 都会被抓走，这是有意的。
- 占用期间鼠标的移动、左中右键按下与抬起、滚轮全部吞掉。右键菜单是抬起触发的，所以按下一旦被吞，对应的抬起也吞；只有"按下发生在接管之前"的抬起才放行，避免按键卡住。
- 每次武装守卫时自动把终端（Windows Terminal / conhost）最小化，方便截整屏；租约到期自动弹回。
- 一轮桌面操作结束跑 `home`：立刻释放守卫并把终端交还前台。
- 终端是 VS Code 内置终端时不会自动最小化（窗口类不是终端类），整屏截图会带上编辑器。
- 改了 `guard.py` 要重启守卫进程才会生效（旧进程不重载代码）：`guard off`，杀掉 `guard.pid` 里的进程，下次动作自动拉起新版。

## 常用命令

```bash
# 眼睛
$PY $PC shot                                  # 全屏，打印 PNG 路径 + 前景窗口 + 光标 + 屏幕信息
$PY $PC shot --region 100,100,800,600 --scale 2
$PY $PC shot --window 123456 --out /tmp/w.png # 按 HWND 截窗口
$PY $PC windows                               # 按 Z 序列出可见窗口（app/title/hwnd）
$PY $PC foreground                            # 当前前台窗口
$PY $PC pos                                   # 光标位置
$PY $PC guard status                          # 鼠标守卫是否在占用
$PY $PC guard on --ttl 60                     # 手动占用 60 秒
$PY $PC guard off                             # 立即释放
$PY $PC home                                  # 释放 + 终端回前台，收工用
$PY $PC batch '[["click","800","450"],["wait","300"],["type","文本","--mode","paste"]]'
                                              # 一次进程连跑多条，省每步 ~0.14s 启动和往返

# 手
$PY $PC activate RobotStudio                  # 已开则切过去（不会启动新进程）
$PY $PC focus 123456 --pid 789                # 按 HWND 切焦点
$PY $PC click 800 450
$PY $PC click 800 450 --button right
$PY $PC click 800 450 --double
$PY $PC click 800 450 --mods ctrl shift
$PY $PC drag 400 300 700 300
$PY $PC scroll -120                            # 负=向下
$PY $PC key ctrl+s
$PY $PC type "hello"                          # 默认粘贴（会覆盖剪贴板）
$PY $PC type "第一行\n第二行" --mode line --clear   # 逐字敲，可见性好，慢
$PY $PC wait 500
```

输出恒为一行 JSON；失败也有 `ok:false`。退出码 2 = elevated。

## 典型用法

- **操作 GUI 软件**：`shot` 找到控件 → `activate`/点击 → 输入 → `shot` 确认结果。
- **RobotStudio 仿真**：截图确认视图 → 点击播放/停止 → 等仿真推进 → 截图 + 读控制器日志窗口验证。判定对错必须以日志/数值为准，截图只用来定位和取证。
- **填表/代码**：先 `activate` 目标程序，`type --mode line` 让用户看得见输入过程；`--mode paste` 快但会覆盖剪贴板。

## 已知限制

- Chrome/Edge 地址栏换 URL：`key ctrl+l` 选中后必须用 `type ... --mode paste` 覆盖；`--mode line` 的清行逻辑（Home/Shift+End/Delete）在地址栏里不可靠，会把新内容追加到旧 URL 后面。

- 3D 视口、画布类区域无法从像素读出精确数值，只能读界面上显示的数字。
- 用户移动鼠标会打断注入的点击序列。
- 每次调用都是新进程（约 0.14s 启动）。已经知道下一步的固定序列用 `batch` 一次跑完；需要看截图才能决定的步骤必须分开。
- 剪贴板会被 `type` 覆盖，敏感内容操作前留意。
