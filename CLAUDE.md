# ShoppingAgent — 智能导购 Agent

## 项目概述
基于 LangGraph 的多 Agent 智能导购系统。

## 技术栈
- Python 3.11+ / LangGraph / FastAPI
- Qdrant + BGE-M3（本地 Ollama）+ PostgreSQL + Redis
- SQLModel ORM / structlog 日志

## 任务来源
所有任务定义在 `reports/2026-05-14-智能导购Agent-tasks.md`
开发前先读对应任务的描述和产出要求。

## 当前阶段
Phase 0：骨架搭建（T0.1 → T0.7）

## 开发环境
- 虚拟环境：`.venv`（所有依赖安装在此，不要用全局 Python）
- 激活：`source .venv/Scripts/activate`（Git Bash）或 `.venv\Scripts\activate`（CMD）

## 开发流程（严格遵守）
每个小任务按以下步骤执行：

1. **实现**：按任务文档编写代码
2. **单元测试**：对本次改动做简单验证（import 检查、函数调用、关键逻辑断言）
3. **QA**：运行 `/qa` 做质量检查
4. **Git 提交**：commit + push，commit message 格式：`完成 T0.x: <任务简述>`
5. **暂停等待确认**：告知用户当前任务已完成，等用户确认后再开始下一个任务

**不要自动跳到下一个任务。每次必须等我确认。**

## 关于 superpowers / gstack
- 不要主动调用 superpowers 的 skills（如 writing-plans、subagent-driven-development 等）
- gstack 可按需使用，但遵循以下原则：
  - `/qa`：每个任务完成后使用
  - `/review`：仅在完成一个完整 Phase 后使用，或你觉得代码改动较大需要审查时
  - 其他 skills（/ship、/autoplan、/office-hours 等）：不用，除非我明确要求
- 不要一次性调用多个 skills，按需单个调用
- 开发节奏由我控制，不需要自动化流水线

## 开发规范
- 配置集中在 config.yaml，不硬编码
- LLM/Embedding 调用必须 async（asyncio.to_thread）
- State 只存工作记忆，日志走 structlog
- 并行追加字段用 Annotated[list, add] Reducer

## 禁止事项
- 不要在代码中硬编码 API Key / 阈值 / 模型名
- 不要在 State 中存执行日志
- 不要直接在主线程调用 sentence_transformers
