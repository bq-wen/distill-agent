# 治理模型与安全边界

本项目的差异化核心：**工具治理不是外挂，是图运行时的一等公民**。这篇讲清楚 WenGraph 的治理三元组、两种运行模式，以及"公开引用合同"。

## 1. 治理三元组（概念 + 代码）

```python
CapabilityPolicy   # 管"谁有资格用哪个工具"（节点 → 工具授权）
RiskPolicy         # 管"有资格后，当前模式下能不能真执行"（模式 × 风险等级 → 决策）
ToolGuard          # 门卫：执行前依次过能力策略 + 风险策略，输出 PolicyDecision
```

对应代码（vendor/wengraph `tools/policy.py`）：

```python
class ExecutionMode(str, Enum):
    SUPERVISED    # 人工监督
    BOUNDED_AUTO  # 有界自动
    UNATTENDED    # 无人值守

# RiskPolicy.evaluate 规则
LOW    → 恒 ALLOW
MEDIUM → SUPERVISED 下 REQUIRE_APPROVAL，其余 ALLOW
HIGH   → UNATTENDED 下 DENY，其余 REQUIRE_APPROVAL
```

一个工具请求的执行路径：

```
LLM/节点申请工具
  → ToolGuardNode: capability_policy.authorize()  # 没资格 → 异常/拒绝
  → risk_policy.evaluate()                        # 出决策
  → PolicyRouterNode 按 policy_decision 路由
     ALLOW            → ToolNode 执行
     REQUIRE_APPROVAL → ApprovalEndNode 暂停，等人工审批
     DENY             → DenyEndNode 反馈拒绝原因
```

## 2. 两张子图的治理对比（本项目核心叙事）

| | 展示链路 | 蒸馏链路 |
|---|---|---|
| 模式 | `UNATTENDED` | `SUPERVISED` |
| 工具 | 只读检索（READ_ONLY, LOW） | 写审计产物（MEDIUM）/ 写库（HIGH） |
| 效果 | 全自动放行 | 写库前强制 `REQUIRE_APPROVAL` 人工审批 |

**一句话**：同一套门卫，换参数就得到两种安全模型。这正是"自研图运行时"相比"裸调 LLM + 散装工具函数"的价值。

## 3. 能力授权细节（现状代码）

`graph.py` 中：

```python
guard = ToolGuard(CapabilityPolicy(), RiskPolicy(ExecutionMode.UNATTENDED))
for tool in tools:
    guard.capability_policy.register_tool(ToolSpec(name=tool.name,
        required_capabilities={Capability.READ_CODE}, risk_level=RiskLevel.LOW))
guard.capability_policy.grant(llm.name, {Capability.READ_CODE})
```

- 只有 LLM 节点被授予 `READ_CODE` 能力
- 两个检索工具注册为 LOW 风险只读
- 目前只有一条链路，治理模型已就位；蒸馏链路加入后复用同一套机制

## 4. 公开引用合同（安全边界）

这是项目里最值得讲的安全设计之一：

**规则**：HTTP 响应里永远不出现——
- 原始 Markdown 正文（可能是私有材料）
- 本地文件路径
- SQLite 行 / 检索原始结构

**只出现**——
- 前端渲染用的引用卡片：`public_summary` + `public_url`（front matter 中人工审核过的公开元数据）
- 回答正文中的引用来源标题

**实现点**：
- 工具 `_render_matches` 组装检索片段时，`public_citation` 只含公开元数据
- Pydantic 契约（`contracts.py`）在 API 边界强制字段白名单
- README 安全说明：密钥只在 .env，Git/Docker 镜像不含真实 key

## 5. 其它安全/隐私设计

| 设计 | 说明 |
|---|---|
| 无登录会话 | sessionStorage 随机会话 ID，无账号体系、无跨设备同步 |
| 24h TTL | 临时会话与回答 24 小时过期（`PERSONAL_AGENT_CONVERSATION_TTL_HOURS`） |
| 单进程部署约束 | 队列进程内结构；多实例必须换共享调度（Redis/Celery），README 明示 |
| 蒸馏人工审批（已实现） | 蒸馏图跑在 SUPERVISED 模式，写库工具强制 REQUIRE_APPROVAL |

## 6. 面试高频追问

**Q: 你凭什么说比 LangChain 安全？**
A: 不说"更安全"，说"把安全决策做成图的显式节点"：每次工具调用都经过 ToolGuardNode → 策略路由，决策可观测、可测试、可扩展（加新策略 = 加节点/改规则，不动工具实现）。

**Q: REQUIRE_APPROVAL 之后图怎么恢复？**
A: ApprovalEndNode 记录 `resume_node_name`（恢复点），审批通过后从该节点继续执行；拒绝则走 DenyEndNode 反馈拒绝原因给 LLM，LLM 据此调整回答。

**Q: 工具是只读的，为什么还要防？**
A: 防的不是"写坏数据"，而是**幻觉与越权**：LLM 可能编造工具结果、可能用工具做超出检索的事。只读 + LOW 风险 + 能力授权，把 LLM 的工具面收敛到最小可用集。

**Q: 有 red-team / 越狱尝试怎么办？**
A: 诚实回答边界：当前是治理框架 + persona 约束（资料不足明说、绝不编造），没有做对抗性防御；后续增强点包括提示注入测试集。面试里"知道自己没做什么"比"吹什么都做了"更可信。
