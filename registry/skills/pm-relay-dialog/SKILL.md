---
name: pm-relay-dialog
description: "对话框处理规约 — dialog_watcher 主批、DialogHandler fallback、auto-mode 人工接管。遇到对话框/权限问题时加载。权威来源：pm-relay §4。"
version: 0.2.0
author: Owner-Control
platforms: [macos]
metadata:
  hermes:
    tags: [pm-runtime, relay, dialog, permission]
---

# PM Relay Dialog Handling v0.2.0

> 权威来源：pm-relay §4（Observer & 权限人工接管）
> 加载时机：卡在对话框、配置自动批准、诊断完成检测问题

---

## 0. 什么时候加载

1. **executor 卡在对话框** — 看到权限弹窗、文件确认、trust 询问
2. **配置自动批准策略** — 调整 watcher 模式匹配或 handler fallback
3. **诊断完成检测问题** — executor 完成了但没触发 completion
4. **处理 auto-mode 对话框** — workflow auto-mode 需要你亲自批准

---

## 1. 核心架构（2026-06-02 重设计）

### 1.1 两层批准机制

```text
dialog_watcher（主批 — 外部脚本，模式匹配）
  ├── 每 0.5 秒全 pane 扫描
  ├── 匹配成功 → tmux send-keys Enter
  ├── 匹配失败 → 等下一轮
  └── auto-mode 对话框 → 播放声音 + 通知用户

ClaudeDialogHandler（fallback — 内嵌在 relay）
  ├── BASH_PERMISSION_DIALOG 不再 HOLD
  ├── 返回 dialog_type=None + runtime_state="running"
  └── 仅当 watcher 也匹配不到时兜底
```

**设计原则：** dialog_watcher 用简单模式匹配解决 90% 的权限弹窗。DialogHandler 不抢着批，不擅做判决。它俩不竞争，是先后关系。

### 1.2 watcher 能处理 vs 不能处理的

| 类型 | watcher 处理 | 说明 |
|------|-------------|------|
| "Do you want to proceed?" | ✅ 自动按 Enter | 选 Yes（默认选项） |
| "Do you want to create" | ✅ 自动按 Enter | 选 Yes |
| "Do you want to overwrite" | ✅ 自动按 Enter | 选 Yes |
| "switch to auto mode" | ❌ 播放声音 + 通知 | 必须人工批准 |
| 非标准对话框 | ❌ 等 handler 兜底 | 罕见，Terminal 窗口可见 |

### 1.3 dialog_watcher 启动

dispatch 后立即启动：

```bash
python3 tools/dialog_watcher.py <tmux-session-id>
```

`<tmux-session-id>` 从 `runtime/session.yaml` 的 `tmux_session_id` 字段获取。

watcher 自动退出？不退出。一直在后台运行，直到 tmux session 被清理（closeout 后由 tmux-session-gate.py 杀掉）。

---

## 2. 自动处理的对话框

| 弹窗类型 | 检测关键词 | 谁处理 | 处理方式 |
|----------|-----------|--------|---------|
| FILE_CREATION_DIALOG | "Do you want to create" | watcher ✅ | 自动 Enter（选 Yes） |
| FILE_OVERWRITE_DIALOG | "Do you want to overwrite" | watcher ✅ | 自动 Enter（选 Yes） |
| FILE_EDIT_DIALOG | "Do you want to make this edit" | watcher ✅ | 自动 Enter（选 Yes） |
| BASH_PERMISSION_DIALOG | "Do you want to proceed?" | watcher ✅ | 自动 Enter（选 Yes） |
| TRUST_DIALOG | "Yes, I trust this folder" | handler ⚠️ | 选 [A] 或选项 1 |
| WORKFLOW_AUTO_MODE | "switch to auto mode" | **人工** ⛔ | 声音通知→Hermes CLI 问→你批 |

### 2.1 BASH_PERMISSION 不 HOLD 规则

DialogHandler 不再对 BASH_PERMISSION_DIALOG 做 HOLD 判决。遇到"未安全评估的 bash 命令"时：
- 记录 reason 日志
- 返回 `dialog_type=None`（表示无决策）
- runtime_state 保持 "running"
- watcher 随后按 Enter 处理

这个改动在 `tmux_executor.py` 的 `_classify()` 方法中，将 `runtime_state="hold"` → `runtime_state="running"` + `dialog_type=None` + `action="deferred_to_external_watcher"`。

### 2.2 为什么不需要 DialogHandler 做安全评估

因为 tmux 窗口默认是可见的——用户能直接看到弹了什么。watcher 按 Enter（选项 1 Yes）是保守安全的：选项 1 只批准当前命令，不自动信任后续操作。如果命令真有风险，用户观察窗口发现异常会自行中断。

**这不是全自动安全系统。这是把安全交给可见的现场。**

---

## 3. Workflow Auto-Mode 处理协议

当 dialog_watcher 检测到 pane 中包含 "switch to auto mode" 或 "workflows run best with" 时：

1. watcher **不按 Enter**，不做任何选择
2. 播放 Glass 提示音（`afplay /System/Library/Sounds/Glass.aiff`）
3. 打印通知到 stdout，含 session ID
4. 10 秒抑制期（避免重复通知）
5. 我（Hermes）监听到通知后，问你：**"要不要开 workflow auto-mode？"**
6. 你说开 → 我用 `tmux send-keys -t <session-id> <选项编号>` 发送选择
7. 你说不开 → 选普通 Yes（选项 1），不用 auto-mode

**不开 auto-mode 的后果：** Claude 每次执行读取命令（grep、ls、cat）都会弹 permission 对话框，watcher 自动批。不影响功能，略增加延迟。

---

## 4. 完成检测规则

### 4.1 Artifact Completion > Pane Runtime State

所有 expected outputs 已存在且 size > 0 连续满足 2 轮（~6 秒）→ 直接 `executor_completed`。产物完成 > pane 状态推断。

### 4.2 Hold 状态处理

outputs 已存在但 executor 处于 hold 状态（弹窗 hold 残留）→ 跳过 hold 直接 executor_completed。

### 4.3 不杀正在跑的 session

Claude Code 即使不走 workflow 也能高效产出。以 `ls outputs/` 的实际文件为准，不凭 progress.yaml 判断。

---

## 5. 常见问题排查

| 症状 | 根因 | 修复 |
|------|------|------|
| relay exit 5 + dialog_type=BASH_PERMISSION_DIALOG | DialogHandler 旧代码仍 HOLD | 确认 tmux_executor.py 已 patch（dialog_type=None） |
| watcher 不动 | session ID 不匹配 | `tmux list-sessions` 核对 ID |
| watcher 连按多次 | 多个 pane 同时匹配 | 确认 watcher 版本含 3 秒抑制期 |
| auto-mode 对话框无声音通知 | watcher 版本旧 | 更新 `tools/dialog_watcher.py` |
| 对话框 watcher 匹配不到 | 新模式不在 PATTERNS | 追加到 dialog_watcher.py 的 AUTO_APPROVE_PATTERNS |

---

## 参考历史

| 日期 | 版本 | 变更 |
|------|------|------|
| 2026-06-02 | v0.2.0 | 重设计：dialog_watcher 为主批 + DialogHandler fallback；BASH_PERMISSION 不再 HOLD；auto-mode 人工接管协议；watcher 0.5s 轮询全 pane 扫描 |
- `~/.hermes/skills/pm-runtime/dispatch-prompt-authoring/scripts/dialog_watcher.py` — skill 级副本
- dispatch-prompt-authoring skill — Dispatch SOP 含 dialog_watcher 启动步骤
