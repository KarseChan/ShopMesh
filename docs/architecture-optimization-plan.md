# Agent 架构优化方案

基于 [learn-claude-code](https://github.com/shareAI-lab/learn-claude-code) S01-S20 对比分析。

参考文档位置: `docs/references/learn-claude-code/`

---

## 优先级排序

| 优先级 | 模块 | 原因 | 参考章节 |
|--------|------|------|----------|
| **P0** | Error Recovery | 直接导致系统不稳定 | `s11_error_recovery/` |
| **P0** | Subagent 隔离 | 状态污染导致行为不可预测 | `s06_subagent/` |
| **P1** | Task 认领 | Orchestrator 规则被忽略的根因 | `s12_task_system/` |
| **P1** | Context Compact | 长对话会崩溃 | `s08_context_compact/` |
| **P2** | Hooks 系统 | 扩展性，不影响核心功能 | `s04_hooks/` |
| **P2** | Nag Reminder | 长任务体验优化 | `s05_todo_write/` |
| **P3** | Team 协作 | 高级功能，当前不需要 | `s15_agent_teams/`, `s16_team_protocols/` |

---

## P0-1: Error Recovery

**目标**: 在 `_run_agent_loop` 中添加错误恢复机制

**参考**: `s11_error_recovery/README.md` + `s11_error_recovery/code.py`

**实现要点**:
1. 添加 `RecoveryState` 追踪恢复状态
2. LLM 调用失败时指数退避重试 (min(500*2^attempt, 32000) + 25% jitter)
3. `prompt_too_long` 错误时触发 reactive_compact 后重试
4. 输出截断时发送 continuation prompt (max_tokens 8K->64K)
5. 连续 529 错误切换 fallback 模型

**修改文件**: `src/graph/specialized_agents.py`

---

## P0-2: Subagent 隔离

**目标**: DAG executor 中的 Agent 任务使用隔离状态

**参考**: `s06_subagent/README.md` + `s06_subagent/code.py`

**实现要点**:
1. 子 Agent 只接收精简状态 (messages[-3:], entities, user_id)
2. 添加 30 轮安全限制
3. 只返回最终摘要，不污染父状态
4. 禁止递归调用 Agent 任务

**修改文件**: `src/graph/dag_executor.py`

---

## P1-1: Task 认领机制

**目标**: 把 Orchestrator 的 missing_critical_fields 检查做成确定性逻辑

**参考**: `s12_task_system/README.md` + `s12_task_system/code.py`

**实现要点**:
1. 在 `_build_deterministic_dag` 中硬编码 missing_fields 检查
2. 添加 `claim_task` 依赖阻塞机制
3. DAG 结果持久化到 Redis (跨会话)

**修改文件**: `src/graph/orchestrator.py`

---

## P1-2: Context Compact

**目标**: 添加多层压缩 + 熔断器

**参考**: `s08_context_compact/README.md` + `s08_context_compact/code.py`

**实现要点**:
1. L1 snip_compact: 截断旧消息，保留头尾
2. L2 micro_compact: 旧 tool_result 替换为占位符
3. L3 tool_result_budget: 大输出持久化到 Redis
4. L4 compact_history: LLM 生成摘要 (已有)
5. 熔断器: 连续 3 次压缩失败停止

**修改文件**: `src/memory/session_memory.py`

---

## P2-1: Hooks 系统

**目标**: 添加可扩展的钩子机制

**参考**: `s04_hooks/README.md` + `s04_hooks/code.py`

**实现要点**:
1. 创建 `src/graph/hooks.py` 钩子注册表
2. 四个事件点: pre_tool_use, post_tool_use, pre_llm_call, post_llm_call
3. 钩子可阻断执行 (返回 block=True)

**新增文件**: `src/graph/hooks.py`
**修改文件**: `src/graph/specialized_agents.py`, `src/graph/tool_executor.py`

---

## P2-2: Nag Reminder

**目标**: 长任务时提醒 Agent 继续

**参考**: `s05_todo_write/README.md` + `s05_todo_write/code.py`

**实现要点**:
1. 追踪 `last_tool_call_iteration`
2. 连续 3 轮无工具调用时注入提醒消息
3. 提醒内容: "请继续完成任务，如果需要更多信息请调用 ask_clarification"

**修改文件**: `src/graph/specialized_agents.py`

---

## P3: Team 协作 (暂不实现)

**目标**: 多 Agent 团队协作

**参考**:
- `s15_agent_teams/README.md` - 文件邮箱通信
- `s16_team_protocols/README.md` - 请求-响应握手
- `s17_autonomous_agents/README.md` - 自组织任务认领

**说明**: 当前系统是单 Agent + DAG 执行，不需要完整的 Team 协作。如果未来需要多 Agent 并行决策，再实现。

---

## 实施顺序

```
Phase 1 (稳定性):
  P0-1 Error Recovery → P0-2 Subagent 隔离 → P1-1 Task 认领

Phase 2 (健壮性):
  P1-2 Context Compact → P2-2 Nag Reminder

Phase 3 (扩展性):
  P2-1 Hooks 系统
```
