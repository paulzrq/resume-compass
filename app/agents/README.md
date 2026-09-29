# Agents 升级说明（`agent-upgrade` 分支）

多智能体打分流水线：`plan → score ⇄ critique → report`，LangGraph 实现。

## 已搭好的脚手架

| 文件 | 内容 |
|---|---|
| `agents/state.py` | `AgentState`：全图共享状态 |
| `agents/graph.py` | `build_graph(api_key, model, scorer_agent=None)`：拓扑 + 条件边 |
| `agents/nodes.py` | plan/score/critique/report 四个 node 的接线；LLM 调用走 `_call_llm`（可 monkeypatch） |
| `agents/tools.py` | `build_tools(resume_text)`：`get_dimension_rubric`、`verify_quote` 已实现 |
| `agents/prompts.py` | 三个 prompt 构造器（待实现，有契约 docstring） |
| `tests/test_agent_graph.py` | 离线测试（fake scorer + monkeypatch，无需 API key） |

prompt 没写完之前，nodes 走 FALLBACK 逻辑（默认取证计划、critic 默认放行），
图可以跑通、接线可验证。prompt 写好后自动切换真实逻辑。

## Paul 手写清单（按顺序）

1. **`agents/prompts.py`**：`build_planner_prompt` → `build_scorer_prompt` →
   `build_critic_prompt`。每个函数头上有契约 docstring；措辞复用
   `scoring.build_system_prompt` 里验证过的部分（35字理由、逐字证据、先摘证据再打分）。
2. **`agents/tools.py::search_jd_library`**：v1 先做关键词检索（docstring 里有路线），
   跑通后再考虑向量检索。
3. **`agents/nodes.py::report_node`**：补上确定性总分（加权 + `scoring._score_cap` 封顶 +
   tiers 定级），输出键与现有 `score_resume()` 对齐。
4. **eval harness**（新目录 `eval/`）：20–30 份简历人工打分 → 跑新旧两套流水线 →
   算模型-人工一致性（Spearman correlation / exact-match rate）→ 输出对比报告。

每完成一步跑：`python -m pytest tests/test_agent_graph.py -v`（离线部分），
再用真实 API 跑通 1–2 份简历端到端。

## 依赖

`app/requirements.txt` 新增：`langgraph`、`langchain-anthropic`
（`langchain-core` 会被自动带上）。

## 主分支保护

`main` 分支的现有 Streamlit 应用不动；升级全部在 `agent-upgrade` 分支上做，
`app.py` 切到新流水线是最后一步（等 eval 证明新链路不差于旧链路再切）。
