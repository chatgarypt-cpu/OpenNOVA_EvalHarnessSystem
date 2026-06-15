1|# OpenNOVA EvalHarnessSystem — 工作流 v4.3 现状描述
2|
3|> 文档类型：workflow_core.md v4.3 现状描述
4|> 当前范围：§0–§14
5|> 状态：draft / 基于 v4.2 Reality Review + G1-G6 实现 + Control Agent 决策
6|> 生成日期：2026-06-04
7|> 用途：描述 OpenNOVA 现在的工作流形式、角色模块和能力。给 Control Agent / Hermes / Reviewer Team 做上下文参考。
8|> 注意：本文基于真实代码和注册表现状，不是 design intent。
9|
10|---
11|
12|# §0 文档定位与权威源
13|
14|本文是 OpenNOVA 工作流的现状描述文件。
15|
16|## 0.1 权威源层级
17|
18|```text
19|1. WorkflowBase/ 下的注册表 + 代码 = 真实权威
20|2. 本文 = 现状描述 + 架构决策记录，不是 design intent
21|3. docs/design/ 下 v4.0 及之前的旧设计文档已被确认过时
22|```
23|
24|## 0.2 本文回答三个问题
25|
26|```text
27|1. 现在的工作流长什么样？
28|2. 有哪些主要角色和模块？
29|3. 有哪些能力？
30|```
31|
32|---
33|
34|# §1 v4.3 核心目标
35|
36|v4.3 不是新设计，而是基于 Reality Review + G1-G6 实现后的现状基线和架构决策记录。
37|
38|## 1.1 核心模型：四层工作流
39|
40|```text
41|旧工作流（隐式的、靠口头传的）：
42|Owner 说需求 → Hermes 找人执行 → 执行完告诉 Owner → Owner 判断
43|
44|现在的工作流（四层显式化）：
45|第 1 层：Owner Brief    ← 人话摘要，30 秒看完
46|第 2 层：Iteration Contract  ← Control Agent + Hermes 读
47|第 3 层：Runtime Dispatch     ← Hermes 自动生成
48|第 4 层：Evidence & Closeout  ← Runtime 自动回收 + Owner 收口
49|```
50|
51|```mermaid
52|flowchart TD
53|    subgraph L1["第 1 层：Owner Brief（人话摘要）"]
54|        OB["7 个问题<br/>30 秒看完<br/>给你看的"]
55|    end
56|    subgraph L2["第 2 层：Iteration Contract（迭代合同）"]
57|        IC["锁目标/边界/验收<br/>Control Agent + Hermes 读"]
58|    end
59|    subgraph L3["第 3 层：Runtime Dispatch（运行时调度）"]
60|        RD["Hermes 自动生成<br/>task_config.yaml / prompt.md<br/>监控自动挂载"]
61|    end
62|    subgraph L4["第 4 层：Evidence & Closeout（证据与收口）"]
63|        EC["Runtime 自动回收证据<br/>产物校验 / 日志写入<br/>Owner-Control 收口"]
64|    end
65|
66|    OB --> IC --> RD --> EC
67|```
68|
69|## 1.2 v4.3 的关键变化
70|
71|相对于 v4.0 时期（以设计文档为准）：
72|
73|| 变化 | v4.0 时期 | v4.3 现状 |
74||------|-----------|----------|
75|| 权威源 | workflow_core_v4.0_r2.md（197KB 设计文档） | WorkflowBase/ 注册表 + 代码 |
76|| 模板 | 无统一模板，多版本混用 | v4.2 iteration contract + 选型规则 |
77|| 执行器 | Codex 被绑死写日志 | relay_runner 后处理回调，不绑 executor |
78|| 监控 | 无自动 idle 检测 | dialog_watcher 30s 自动推 |
79|| 证据链 | 无 expected_outputs 校验 | G5 产物完整性检查 |
80|| 日志 | 人工写 TASK_LOG | G2/G4 自动 append |
81|| 工作地图 | compact YAML 设计未实现 | workflow_map.yaml 已由 --generate-map 生成 |
82|| 声音 | 固定 | public/private profile 切换 |
83|| 工具链 | 部分在 workyb 实验线 workyb/ | 全部迁入 工作流自包含 |
84|
85|---
86|
87|# §2 核心原则
88|
89|OpenNOVA 工作流目前基于 8 条核心原则运行。
90|
91|## 2.1 原则列表
92|
93|```text
94|1. 人先看 Owner Brief，机器再读 Contract。
95|2. Codex 默认单节点，Claude workflow 才默认 DAG。
96|3. Hermes 永远是 PM Runtime，不让工人当工头。
97|4. Iteration Plan 只锁目标、边界、验收，不写成 Runtime 说明书。
98|5. Dispatch / task_config 由 Hermes 从合同生成，不让你手写。
99|6. Evidence 是硬门：没有 expected_outputs / receipt / result，就不算完成。
100|7. Closeout 只能 Owner-Control 做，PM summary 不等于 closeout。
101|8. workyb 实验线能力通过 promotion 进入 OpenNOVA，不再无限停在实验场。
102|```
103|
104|## 2.2 原则详解
105|
106|### 原则 1：人先看 Owner Brief，机器再读 Contract
107|
108|Owner Brief 是放在迭代计划顶部的 7 个问题。这是唯一写给 Owner 看的部分。看完 Owner Brief 你就能决定"这轮值不值得做"、"要不要批"。
109|
110|Contract 写给 Control Agent + Hermes。Control Agent 用它做调度决策，Hermes 用它生成 dispatch。Owner 不需要读 Contract 全文。
111|
112|### 原则 2：Codex 默认单节点，Claude 才默认 DAG
113|
114|Codex 是高约束工程执行器，适合改一个模块、补一个 validator、修一个 registry。不默认 agent team，不默认多节点 DAG。
115|
116|Claude Code workflow 适合多文件盘点、Reality Review、并行审查、课程作业。需要多节点编排时才走 DAG。
117|
118|```text
119|Codex = 稳定施工队
120|Claude workflow = 灵活项目组
121|Hermes = 工头
122|```
123|
124|### 原则 3：Hermes 永远是 PM Runtime，不让工人当工头
125|
126|Hermes 负责调度、派发、回收、监控。不写业务代码，不做 closeout。Codex 和 Claude Code 是被 Hermes 调度的 worker。
127|
128|### 原则 4：Iteration Plan 只锁目标、边界、验收
129|
130|迭代计划回答：做什么、不做什么、做完怎么看。不写成 Runtime 说明书。Runtime 细节（task_config、dispatch 路径、prompt 格式）由 Hermes 从合同自动生成。
131|
132|### 原则 5：Dispatch 由 Hermes 从合同生成
133|
134|Control Agent 写好 Iteration Contract 后，Hermes 自动填充 task_config.yaml、选择 executor、生成 dispatch/prompt.md。Owner 不需要手写任何 dispatch 配置。
135|
136|### 原则 6：Evidence 是硬门
137|
138|没有 expected_outputs → 没有 receipt → 没有 result → 不算完成。G5 实现确保 relay_runner 收尾时自动校验。
139|
140|### 原则 7：Closeout 只能 Owner-Control 做
141|
142|PM Runtime 可以写 summary，但 summary 不等于 closeout。closeout 必须由 Owner 明确决策，走 `task-status-writer.py` 校验。
143|
144|### 原则 8：workyb 实验线能力通过 promotion 进入 OpenNOVA
145|
146|workyb 实验线（workyb）是实验场。成熟能力通过 B→A promotion gate 进入 OpenNOVA，不长期停在实验场。
147|
148|---
149|
150|# §3 角色分工
151|
152|## 3.1 角色总览
153|
154|```mermaid
155|flowchart LR
156|    Owner["Owner (Gary)"] -->|定方向| CA["Control Agent"]
157|    CA -->|写迭代合同| H["Hermes (PM Runtime)"]
158|    H -->|dispatch| CX["Codex"]
159|    H -->|dispatch| CC["Claude Code"]
160|    CX -->|receipt| H
161|    CC -->|review| RT["Reviewer Team"]
162|    RT -->|report| H
163|    H -->|evidence| Owner
164|    Owner -->|closeout| H
165|```
166|
167|## 3.2 Owner (Gary)
168|
169|Owner 是项目方向与最终审批权威。
170|
171|**负责：**
172|
173|```text
174|1. 提出项目目标或版本需求；
175|2. 判断是否接受 Control Agent 的推进方案；
176|3. 审阅 dispatch-approval-gate 确认派发；
177|4. 审阅关键文档和 closeout 判断；
178|5. 对方向、边界、节奏作最终决策。
179|```
180|
181|**不负责：**
182|
183|```text
184|1. 人工复制长任务书给每个 Agent；
185|2. 人工轮询长任务进度；
186|3. 人工整理执行器产物路径。
187|```
188|
189|**批准规则：**
190|
191|```text
192|以下情况不得视为批准：
193|1. Owner 沉默；
194|2. Owner 超时未回复；
195|3. Owner 模糊表达；
196|4. 历史偏好；
197|5. PM Runtime 自行判断"应该可以"；
198|6. DS pass 不等于 Owner 批准；
199|7. Codex delivered 不等于 Owner 批准。
200|```
201|
202|## 3.3 Control Agent
203|
204|Control Agent 是调度层决策者。
205|
206|**负责：**
207|
208|```text
209|1. 定 scope：做什么、不做什么；
210|2. 选模板：S/M/L/B 四档；
211|3. 选执行器：Codex / Claude Code / 混合；
212|4. 选执行模式：single_node / agent_team / workflow_dag；
213|5. 写 Owner Brief。
214|```
215|
216|**不负责：**
217|
218|```text
219|1. 不替 Hermes 写 dispatch（task_config、prompt）；
220|2. 不替 Owner 做 closeout。
221|```
222|
223|## 3.4 Hermes / PM Runtime
224|
225|Hermes 是调度和执行中台。
226|
227|**负责：**
228|
229|```text
230|1. 根据 Iteration Contract 生成 task_config.yaml；
231|2. 通过 executor_registry 选择执行器；
232|3. 执行 relay dispatch（init → launch → monitor → collect）；
233|4. 启动 dialog_watcher + heartbeat_monitor；
234|5. 回收 evidence（result.json、receipt、pane_capture）；
235|6. 写 PM Runtime summary（不等于 closeout）；
236|7. 触发 G5 产物校验 + G2/G4 日志写入。
237|```
238|
239|**不负责：**
240|
241|```text
242|1. 不写业务代码；
243|2. 不做 closeout 决策；
244|3. 不替 Owner 做方向判断；
245|4. 不以"看起来不对"杀掉正在跑的 session。
246|```
247|
248|## 3.5 Codex
249|
250|Codex 是高约束工程执行器。
251|
252|**负责：**
253|
254|```text
255|1. 按 iteration contract 执行单模块改动；
256|2. 写测试、跑测试；
257|3. 输出 receipt / result；
258|4. 按授权在允许路径内落盘。
259|```
260|
261|**不负责：**
262|
263|```text
264|1. 不自行决定范围；
265|2. 不默认 agent team；
266|3. 不默认多节点 DAG；
267|4. 不自行 closeout。
268|```
269|
270|## 3.6 Claude Code
271|
272|Claude Code 是灵活执行器，支持 agent team + workflow DAG。
273|
274|**适用场景：**
275|
276|```text
277|1. Reality Review（workflow-reality-review / code-reality-review）；
278|2. 多文件盘点；
279|3. 并行审查（Reviewer Team）；
280|4. 课程作业 / demo pipeline；
281|5. 复杂迁移前对账。
282|```
283|
284|## 3.7 Reviewer Team
285|
286|并行审查团队（原 DS Team），负责事实核查。
287|
288|**负责：**
289|
290|```text
291|1. 结构完整性检查；
292|2. 能力真实性验证；
293|3. 一致性审查；
294|4. 边界与风险评估；
295|5. 合成审查报告。
296|```
297|
298|**不负责：**
299|
300|```text
301|1. 不替 Control Agent 做决策；
302|2. 不替 Owner 做 closeout。
303|```
304|
305|---
306|
307|# §4 第一层：Owner Brief
308|
309|## 4.1 定位
310|
311|Owner Brief 是放在每次迭代计划顶部的 7 问题摘要。**写给 Owner 看**。30 秒能读完。
312|
313|Control Agent 在写完迭代合同后填写 Owner Brief。它不包含 YAML、不包含技术细节、不包含调度配置。只有 Owner 需要知道的信息。
314|
315|## 4.2 7 个问题
316|
317|```text
318|1. 这轮干嘛？
319|   一句话目标。如"给 relay_runner 加一个收尾产物校验"。
320|
321|2. 为什么现在做？
322|   触发原因。如"G5 是 Control Agent 判定 Go2 前必须修的唯一缺口"。
323|
324|3. 谁调度？谁执行？
325|   如"调度：Hermes；执行：Codex（单节点）"。
326|
327|4. 单节点还是多节点？
328|   single_node / workflow_dag。单节点不改其他模块，多节点需多个 executor。
329|
330|5. 会碰哪些文件？
331|   核心改动列表，3-5 个。不说全部，只说关键的。
332|
333|6. 做完我看什么？
334|   验收产物。如"语法检查通过 + expected_outputs_validation.json 生成"。
335|
336|7. 什么情况必须停？
337|   如"改了 contract 之外的文件"、"expected_outputs 检查阻塞了正常流程"。
338|```
339|
340|## 4.3 格式要求
341|
342|```markdown
343|## 第 1 层：Owner Brief（人话摘要）
344|
345|**1. 这轮干嘛？**
346|_{一句话}_
347|
348|**2. 为什么现在做？**
349|_{理由}_
350|
351|...（7 个问题）
352|```
353|
354|Owner Brief 放在迭代合同顶部，`## 第 1 层：Owner Brief` 标题下，清晰分隔。
355|
356|---
357|
358|# §5 第二层：Iteration Contract
359|
360|## 5.1 定位
361|
362|迭代合同锁死目标、边界、验收。给 Control Agent + Hermes 读。Owner 只需要看 Owner Brief。
363|
364|合同由 Control Agent 在启动新迭代时填写，基于 `_template_v4.2_iteration_contract.md`。
365|
366|## 5.2 合同结构
367|
368|```text
369|0. Record Protocol（记录协议）
370|   skill_loaded / record_type / blocker_status
371|
372|1. Version Info（版本信息）
373|   版本号 / 基于版本 / 日期 / 状态
374|
375|2. Control Agent Decision（调度决策）
376|   control_agent / owner_approval_required / task_level
377|
378|3. Goal & Boundary（目标与边界）
379|   goal / allowed_paths / forbidden_files
380|
381|4. Review / Audit（审查范围）
382|   reviewer_team / review_type / review_outputs
383|
384|5. File Change Scope（文件改动范围）
385|   expected_new_files / modified_files / deleted_files / unchanged_files
386|
387|6. Execution Strategy（执行策略）
388|   orchestrator / execution_shape / primary_executor / observer_mode
389|
390|7. Verification Plan（验证方案）
391|   expected_outputs / artifact_validation / capture_stdout
392|
393|8. Runtime Evidence Requirement（运行时证据要求）
394|   receipt / result / logs
395|
396|9. Acceptance & Closeout（验收与收口）
397|   acceptance_criteria / closeout_record
398|
399|10. Notes / Carry-over（备注与遗留项）
400|    minor issues / future backlog
401|```
402|
403|## 5.3 字段详解
404|
405|### Record Protocol
406|
407|```yaml
408|skill_loaded: 加载本模板时加载的 skill 名称
409|record_type: iteration_plan
410|blocker_status: none | present | not_checked
411|artifact_quality: pass | fail | not_checked
412|closeout_eligible: false
413|```
414|
415|### Execution Strategy
416|
417|| 字段 | 值 | 说明 |
418||------|-----|------|
419|| execution_shape | single_node / agent_team / workflow_dag | 默认 single_node |
420|| primary_executor | Codex / Claude Code | Codex 默认单节点 |
421|| observer_mode | true / false | 弹 Terminal 窗口 |
422|| dag_nodes | [] | 仅多目标时填写 |
423|
424|### Verification Plan
425|
426|```yaml
427|expected_outputs:
428|  - outputs/<文件>    # relay_runner 据此做 G5 产物检查
429|artifact_validation: true
430|capture_stdout: true
431|capture_pane: true
432|heartbeat_monitor: true
433|```
434|
435|---
436|
437|# §6 模板选型与执行模型
438|
439|## 6.1 模板选型
440|
441|```mermaid
442|flowchart TD
443|    T["任务等级"] --> S["S-Level<br/>文档修正/路径检查"]
444|    T --> M["M-Level<br/>日常迭代/代码补丁"]
445|    T --> L["L-Level<br/>底座/架构变更"]
446|    T --> B["workyb 实验线<br/>多节点产出/课程"]
447|
448|    S -->|极简卡| R1["Brief + Scope + Outputs"]
449|    M -->|默认| R2["_template_v4.2_iteration_contract.md"]
450|    L -->|备选| R3["_template_v4.0_full.md"]
451|    B -->|Goal DAG| R4["Goal-driven DAG 格式"]
452|```
453|
454|| 等级 | 适用场景 | 模板 | 审查 | closeout profile |
455||------|---------|------|------|-----------------|
456|| **S** | 文档修正、路径检查、单点审查 | 极简卡 | 不需要 | smoke |
457|| **M** | 日常迭代、代码补丁、registry 小修 | v4.2 contract | 可选 | standard |
458|| **L** | OpenNOVA、PM Runtime、跨系统 | v4.0 full | 前置审查 | full_dag |
459|| **workyb 实验线** | 多节点产出、课程作业、demo | Goal-driven DAG | — | — |
460|
461|## 6.2 执行模型
462|
463|```text
464|Codex = single_node（默认），高约束工程落盘
465|Claude Code = agent_team（审查）或 workflow_dag（多目标编排）
466|Hermes = PM Runtime，不直接执行
467|```
468|
469|### Codex 默认 single_node
470|
471|```text
472|Codex 不默认 agent team，不默认多节点 DAG。
473|适合：
474|- 改一个模块；
475|- 补一个 validator；
476|- 修一个 registry；
477|- 写一个 hook；
478|- 做一次受控落盘。
479|```
480|
481|### Claude Code 什么时候用 agent_team / workflow_dag
482|
483|```text
484|Claude Code agent team = 并行审查
485|适合：Reality Review、code review、registry review
486|
487|Claude Code workflow = 多目标 / 多节点 / 快速编排
488|适合：多文件盘点、课程作业、demo pipeline、复杂迁移前对账
489|```
490|
491|---
492|
493|# §7 第三层：Runtime Dispatch
494|
495|## 7.1 执行管道
496|
497|Hermes 根据 Iteration Contract 自动生成 dispatch，不让你手写。
498|
499|```mermaid
500|flowchart LR
501|