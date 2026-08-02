# 蒸馏链路（知识处理流水线）详解

> 已实现。`personal_agent/distillation/` 包含完整流水线：契约、节点、工具、图装配、runner 与 CLI（`distill run` / `distill approve`）。

## 1. 为什么需要蒸馏链路

把原始资料（聊天记录、简历、README、Git 提交）**直接灌进知识库**有三个问题：

1. **噪音**：口语、表情、上下文断裂 → 检索命中差
2. **隐私**：原始聊天含他人信息，不能上公网
3. **形态不对**：检索要的是"第一人称的事实/问答对"，不是原始对话流

蒸馏 = **把原始资料提炼成可检索的知识原子**，且每一步可追溯、可审批。

## 2. 流水线节点（一张图）

```
SourceLoader → Cleaner → Extractor(LLM) → Structurer → ContentRouter → AuditGate → Indexer
     │            │            │              │            │           │
   读输入目录    去噪脱敏      LLM提炼        生成 front    人工审批     复用现有
   识别文件类型   统一中间态   知识原子        matter MD     闸门        索引层
```

### 各节点职责

| 节点 | 输入 → 输出 | 关键行为 |
|---|---|---|
| SourceLoader | 目录 → 原始文档列表 | 识别 md/txt/json，记录 `source_file` 溯源路径 |
| Cleaner | 原始文档 → 干净文本 | 去聊天元信息、表情、PII 标记；统一编码 |
| Extractor (LLM) | 干净文本 → `KnowledgeAtom[]` | 提炼语句/QA 对/事实；输出 JSON 带置信度；涉他隐私与闲聊丢弃 |
| Structurer | 原子 → Markdown 文档 | front matter（source_id/project/visibility/public_summary/...） |
| AuditGate | 文档 → 审批产物 | 写 `data/audit/*.json`，触发 `REQUIRE_APPROVAL` 暂停 |
| Indexer | 批准的文档 → SQLite | 复用 `PersonalKnowledgeService.index_document`（embedding + FTS） |

## 3. 知识原子（核心契约）

```python
class KnowledgeAtom(BaseModel):
    content: str            # 第一人称陈述 / QA 对 / 事实
    kind: Literal["statement", "qa_pair", "fact"]
    source_type: Literal["resume", "project_readme", "git_history", "chat_export", "notes", "manual"]
    source_file: str        # 溯源：原始文件相对路径
    extracted_at: datetime
    confidence: float       # LLM 自评 0-1
    review_note: str | None # 人工审核备注
```

**溯源价值**：每一条知识都能回答"这条是从哪来的"。这是面试讲"AI 生成内容的数据治理"的落点——面试官问"LLM 提炼错了怎么办"，答案不是"不会错"，而是"错了能查到源头、能被审批拦下、能回滚"。

## 4. 人工审批闸门（本设计的核心卖点）

- 蒸馏图运行在 `ExecutionMode.SUPERVISED`
- `write_audit_artifact`（MEDIUM）与 `index_documents`（HIGH）工具在 SUPERVISED 模式下 → `REQUIRE_APPROVAL`
- 图执行器暂停 → 你查看 `data/audit/*.json` → CLI 批准/驳回 → 从 `resume_node_name` 恢复
- **未批准原子绝不进入知识库**

面试讲法："蒸馏结果的写入不是自动的——同一套 ToolGuard 机制，在线服务无人值守放行只读检索，后台蒸馏在写库前强制人工审批。治理模型是同一个，参数不同。"

## 5. CLI 用法（已实现）

```bash
# 全量蒸馏（产物先落 audit，批准后才入库）
python3.12 -m personal_agent.distillation.cli distill --input knowledge/examples/raw --database data/knowledge.db

# 增量：只处理内容变化的文件（哈希记录在 data/distill/state.json）
python3.12 -m personal_agent.distillation.cli distill --input ... --incremental

# 审批
python3.12 -m personal_agent.distillation.cli approve <run_id> --all approved
python3.12 -m personal_agent.distillation.cli approve <run_id> --atom <atom_id> rejected --note "涉隐私"
```

## 6. 输入格式优先级

- **v1**（已实现）：Markdown / TXT / JSON——通用、好测、好演示
- **v2**（后续）：微信聊天导出等脏格式（解析器 + 更强的清洗规则）
- 演示素材优先：**项目 README + 面试问答笔记 + Git 提交历史**——产出物对求职最有用（QA 对直接变成面试回答弹药）

## 7. 与展示链路的关系

```
蒸馏链路(后台, SUPERVISED) ──写入──► SQLite 知识库 ──只读检索──► 展示链路(在线, UNATTENDED)
```

- 写路径隔离：蒸馏先写审批产物，批准后才动索引 → 蒸馏失败/未审批不影响在线服务
- 展示链路的引用合同（public_summary/public_url）对蒸馏产物同样生效：公网永远看不到原始资料

## 8. 面试高频追问

**Q: 为什么审批不用 Web UI？**
A: CLI 优先——蒸馏是低频后台操作，CLI + JSON 产物可脚本化、可 CI 化、可审计；Web UI 是后续增强，不做优先级的起点。

**Q: LLM 提炼的准确率怎么保证？**
A: 三层：① 提炼 prompt 只产出事实/QA，不产出观点性虚构；② 每原子带 confidence + 溯源；③ 人工审批闸门兜底。准确率不是"保证不犯错"，而是"错误可被发现、可追溯、可拒绝"。

**Q: 增量蒸馏怎么判断文件变了？**
A: `run --incremental` 先对输入目录逐文件算 SHA-256，与 `data/distill/state.json` 中上次成功运行记录的哈希比对；全部未变则直接跳过，部分变更则只蒸馏变更文件。全量重建去掉 `--incremental` 即可。
