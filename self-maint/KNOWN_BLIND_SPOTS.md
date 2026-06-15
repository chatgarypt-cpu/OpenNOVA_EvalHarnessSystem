# Self-Maint Known Blind Spots

本目录的自维护脚本（scan_proposal / drift_check / proposal_apply）能自动检查 registry YAML 的 schema 合规、路径可达性、文件存在性。

## 扫不出来的东西

以下场景不在自维护脚本的检测范围内，需要手动关注：

| 盲区 | 说明 | 最近一次手动操作 |
|------|------|----------------|
| **新 skill 注册** | `~/.cc-switch/skills/` 下新建了 skill 但 skill_registry.yaml 没登记 | 2026-06-03: workflow-reality-review + registry-reality-review |
| **新 hook 注册** | `~/.hermes/scripts/` 下新建了脚本但 hook_registry.yaml 没记录 | 2026-06-03: reality-review-hook + iteration-gate |
| **新 executor 注册** | runner/ 下新增了 executor 但 executor_registry.yaml 没登记 | — |
| **版本号一致性** | 多处声明的 version 字段自相矛盾 | Go3 曾检出"四重分裂" |
| **配置与实物漂移** | config.yaml 激活了 hook 但 registry 状态没更新 | 2026-06-03: dispatch-approval-gate / reality-review-hook |
| **doc 与 registry 不同步** | README 声明的条目数与实际 YAML 不一致 | Go3 Rego 曾检出 |
