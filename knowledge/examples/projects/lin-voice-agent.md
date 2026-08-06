---
source_id: lin-voice-agent
project: Voice Agent
title: 语音助手 Skill 编排框架
visibility: private
public_summary: 语音助手 Skill 编排：把技能拆成可组合单元，用图运行时做受控执行。
public_url: https://example.com/lin/voice-agent
public_questions:
  - 语音助手的技能编排怎么做？
  - 为什么选择图而不是链来做编排？
topics: [Agent, 语音]
---
# 语音助手 Skill 编排框架

Lin 的业余项目：把语音助手的技能（查天气、定闹钟、控制智能家居）拆成可组合的 Skill 单元，由一个图运行时按用户意图编排执行。

# 为什么用图而不是链

技能之间有依赖和分支（先确认意图再选技能，技能内可能调用多个工具），链式写死流程不灵活；图可以表达分支、循环和并行，还能在节点之间插入治理逻辑。

# 受控执行

所有工具调用走统一门卫：声明能力、评估风险、白名单放行。未授权的技能（比如读短信）默认拒绝，需要用户显式授权。
