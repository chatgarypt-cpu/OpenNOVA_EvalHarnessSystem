# Promotion Gate Pattern — B→A 资产迁移的 Go1/Go2 两阶段门

## 适用场景

B 线已验证底座能力 promote 到 A 线正式技能目录时，不能一次性直接迁移，必须拆成两阶段：

1. **Go1（只读对账）** — 检查接收环境、盘点待迁资产、识别冲突、生成迁移计划
2. **Go2（受控迁移）** — 基于 Go1 报告的 mapping 执行真实迁移

## Gate 脚本

`~/.hermes/scripts/promotion-gate.py`

```bash
python3 ~/.hermes/scripts/promotion-gate.py go1 [--plan-dir PATH]
```

Go1 检查项：
1. 迭代计划已落盘（存在且含 Go1 定义）
2. 没有正在进行的 Go2 任务（禁止并行）
3. 任务目录已创建（dispatch/logs/runtime/outputs/summary）
4. Owner 批准

## Go1 核心规则

- 只读，不修改任何文件
- 不复制资产
- 必须产出：a_line_inventory / b_line_asset_inventory / conflict_matrix / recommended_mapping / go2_migration_plan

## Go2 核心规则

- 必须基于 Go1 mapping 执行
- target exists → HOLD_AND_REPORT，不覆盖
- 必须保留 workyb 源文件（copy not move）
