# 爆款产品灵感智能体项目说明

这是一套面向“爆款产品灵感挖掘”的本地多 Agent 系统。

它的目标不是单纯生成创意，而是把以下流程串起来：

- 爆款趋势搜索
- 爆款信号清洗
- 中枢 Agent 调度
- 场景拆解
- 特征迁移
- 专利风险初筛
- 成本 / 物流估算
- 最终灵感方案生成
- Markdown 报告输出

这套项目既能跑“模拟版”，也能跑“真实数据版”。

---

## 1. 项目目录结构

```text
Codex爆款产品灵感智能体资产包/
├─ .env
├─ .env.example
├─ README.md
├─ api_clients.py
├─ local_agent_simulator.py
├─ main.py
├─ API使用策略与成本控制.md
├─ demo_output.json
├─ config/
│  └─ agent_registry.yaml
├─ prompts/
│  ├─ 00_中枢调度主Agent_SystemPrompt.md
│  ├─ 01_全网爆款溯源挖掘Agent.md
│  ├─ 02_社会新领域空白需求探测Agent.md
│  ├─ 03_全域特征库提取与跨域迁移Agent.md
│  ├─ 04_用户全场景拆解Agent.md
│  ├─ 05_专利竞品前置预警Agent.md
│  └─ 06_文档美化Agent.md
├─ runbook/
│  └─ Codex部署与使用说明.md
├─ schemas/
│  └─ shared_data_contract.yaml
├─ workflows/
│  └─ 多Agent协作工作流.md
├─ runs/
│  └─ run_YYYYMMDD_HHMMSS/
│     ├─ 00_task.json
│     ├─ 01_raw_search.json
│     ├─ 02_cleaned_trend.json
│     ├─ 03_orchestrator_plan.json
│     ├─ 04_feature_matches.json
│     ├─ 05_scenario_reviews.json
│     ├─ 06_patent_search_records.json
│     ├─ 07_ip_reviews.json
│     ├─ 08_draft_output.json
│     ├─ 09_patent_validation.json
│     ├─ 10_cost_logistics_validation.json
│     ├─ 11_final_output.json
│     └─ 12_report.md
└─ outputs/
   └─ 灵感报告_YYYYMMDD.md
```

---

## 2. 每个文件 / 文件夹是做什么的

### 根目录文件

#### `.env`

你的真实环境变量文件。

里面通常放：

- `OPENAI_API_KEY`
- `SERPER_API_KEY` 或 `SERPAPI_API_KEY`
- `GROQ_API_KEY`
- `DEEPSEEK_API_KEY`
- `OPENAI_MODEL_NAME`

这个文件会被：

- [api_clients.py](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/api_clients.py)
- [main.py](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/main.py)

读取。

#### `.env.example`

环境变量模板。

作用：

- 给你或别人快速复制出新的 `.env`
- 说明每个 key 的用途
- 预留未来扩展项，比如专利 API、向量库 API

#### `api_clients.py`

所有外部 API 的统一调用层。

当前已封装：

- OpenAI
- Serper
- Groq
- DeepSeek

建议：

- 后续如果你要切换模型或新增供应商，优先改这里
- 不建议在 `main.py` 里到处直接写 HTTP 请求

#### `local_agent_simulator.py`

本地模拟版脚本。

特点：

- 不调用真实外部接口
- 只用模拟数据跑流程
- 用来测试数据结构、工作流和 JSON 输出是否通顺

适合场景：

- 刚搭完项目先做自测
- 没有 API 额度时验证流程
- 调 schema 和 prompt 时快速冒烟测试

#### `main.py`

真实数据版主程序。

这是整个项目最核心的入口文件。

它负责：

- 搜索公开网页结果
- 调用清洗模型压缩爆款信号
- 调用各个 Agent
- 生成候选方案
- 做专利、成本、物流校验
- 生成最终 JSON
- 生成 Markdown 报告

如果你要真正运行智能体，主要就是跑这个文件。

#### `API使用策略与成本控制.md`

API 使用说明文档。

主要讲：

- 哪个 Agent 优先用哪个模型
- 如何控制 token 成本
- 10 美金额度下怎么分配 API 使用

#### `demo_output.json`

模拟版脚本的示例输出。

主要用于：

- 看最终结构长什么样
- 给你后续做前端 / 数据库存储时参考

---

### `config/`

#### `agent_registry.yaml`

这是整个多 Agent 系统的“路由表”。

它定义了：

- 系统里有哪些 Agent
- 每个 Agent 对应哪个 prompt 文件
- 每个 Agent 什么时候触发
- 不同工作流的执行顺序

如果你要：

- 新增 Agent
- 删除 Agent
- 调整工作流顺序

优先修改这个文件。

---

### `prompts/`

这是所有 Agent 的 system prompt 存放目录。

#### `00_中枢调度主Agent_SystemPrompt.md`

中枢 Agent 提示词。

负责：

- 识别工作流类型
- 统一调度子 Agent
- 定义终审标准
- 组织最终输出结构

#### `01_全网爆款溯源挖掘Agent.md`

爆款趋势 / 存量爆款拆解 Agent。

负责：

- 从公开搜索结果里提炼爆款线索
- 拆痛点、差评、增长原因、可迁移特征

#### `02_社会新领域空白需求探测Agent.md`

新领域机会挖掘 Agent。

负责：

- 挖蓝海
- 找尚未充分商品化的圈层机会

#### `03_全域特征库提取与跨域迁移Agent.md`

特征迁移 Agent。

负责：

- 从现有产品或趋势中提取结构特征
- 迁移到新的产品定义上

#### `04_用户全场景拆解Agent.md`

场景校验 Agent。

负责：

- 判断产品在真实使用链路里成不成立
- 找场景冲突

#### `05_专利竞品前置预警Agent.md`

专利 / 竞品风险预警 Agent。

负责：

- 输出专利、竞品、平台合规风险判断

#### `06_文档美化Agent.md`

Markdown 报告整理 Agent。

负责：

- 读取最终 JSON
- 输出结构清晰、可汇报的 Markdown 产品灵感报告

---

### `schemas/`

#### `shared_data_contract.yaml`

这是整个系统最重要的数据契约文件。

它定义了：

- 输入 task 的结构
- 候选概念结构
- 最终输出结构
- 枚举值
- 一些硬性规则

如果你要：

- 改字段名
- 增加新字段
- 调整最终 JSON 输出格式

先改这个文件，再去改 `main.py` 对应的拼装逻辑。

---

### `workflows/`

#### `多Agent协作工作流.md`

这是业务层的工作流说明文档。

主要描述：

- 工作流 1：存量爆款微创新
- 工作流 2：新领域原生创新
- 工作流 3：跨赛道颠覆性迁移
- 并行 / 串行策略
- 回退机制

这个文件更像“产品说明”和“流程设计书”。

---

### `runbook/`

#### `Codex部署与使用说明.md`

这是给工程接入和部署参考的说明文件。

如果后续你要把本地脚本继续升级为：

- 服务端项目
- Codex 插件
- 自动化工作流

这里的内容会比较有参考价值。

---

### `runs/`

这是每次运行主程序后自动生成的运行日志目录。

每次跑 `main.py`，会新建一个时间戳目录，里面保存整条链路的中间数据。

作用：

- 排查哪一步出错
- 看每个 Agent 的输入输出
- 复盘模型生成过程
- 方便后续做缓存 / 数据积累

---

### `outputs/`

这是最终 Markdown 报告输出目录。

每次跑完后会生成：

- `灵感报告_YYYYMMDD.md`

这是适合直接阅读、转发和汇报的最终文档。

---

## 3. 这个智能体到底怎么运行

下面按“从零到跑起来”的顺序写。

### 第一步：准备 Python 环境

建议版本：

- Python 3.10+

先确认本机有 Python：

```bash
python --version
```

---

### 第二步：安装依赖

当前最少需要：

```bash
pip install requests
```

如果你后面想让 YAML 解析更稳，也可以装：

```bash
pip install pyyaml
```

---

### 第三步：准备 `.env`

如果已经有 `.env`，直接检查即可。

如果没有，可以复制 `.env.example` 为 `.env`，然后填入你自己的 key。

至少建议有：

```env
OPENAI_API_KEY=...
OPENAI_MODEL_NAME=gpt-4o
SERPER_API_KEY=...
GROQ_API_KEY=...
DEEPSEEK_API_KEY=...
```

说明：

- 你当前项目默认兼容 `SERPER_API_KEY`
- 如果后续你想切换为真正的 `SERPAPI_API_KEY`，也可以加进去

---

### 第四步：先跑模拟版确认结构没问题

这是最稳的做法。

运行：

```bash
python "C:\Users\Administrator\Documents\Codex\2026-06-10\files-mentioned-by-the-user-codex\outputs\Codex爆款产品灵感智能体资产包\local_agent_simulator.py"
```

你会得到一个本地模拟输出 JSON。

如果这一步能跑通，说明：

- Python 环境没问题
- 文件结构没问题
- schema 没坏

---

### 第五步：跑真实数据版主程序

运行默认版本：

```bash
python "C:\Users\Administrator\Documents\Codex\2026-06-10\files-mentioned-by-the-user-codex\outputs\Codex爆款产品灵感智能体资产包\main.py"
```

如果你想指定工作流和类目：

```bash
python "C:\Users\Administrator\Documents\Codex\2026-06-10\files-mentioned-by-the-user-codex\outputs\Codex爆款产品灵感智能体资产包\main.py" --workflow workflow_1 --goal "寻找适合亚马逊和抖音的桌面收纳新品" --categories "桌面收纳,居家小件,DIY配件" --seed "磁吸,可拆卸,模块化,透明收纳"
```

参数说明：

- `--workflow`
  - `workflow_1`：存量爆款微创新
  - `workflow_2`：新领域机会
  - `workflow_3`：跨赛道迁移
  - `hybrid`：混合模式

- `--goal`
  - 本次任务的目标描述

- `--categories`
  - 本次优先关注的品类

- `--seed`
  - 本次优先关注的特征关键词

- `--run-name`
  - 可自定义运行目录名称

---

### 第六步：查看输出结果

运行完成后看两个地方：

#### 1. `runs/时间戳目录/`

这里有每一步中间 JSON。

最重要的是：

- `08_draft_output.json`
- `09_patent_validation.json`
- `10_cost_logistics_validation.json`
- `11_final_output.json`
- `12_report.md`

#### 2. `outputs/`

这里有最终汇报版 Markdown：

- `灵感报告_YYYYMMDD.md`

---

## 4. 保姆级使用建议

如果你是第一次用，建议按这个顺序：

1. 先不改任何代码，直接跑模拟版
2. 再跑真实版默认参数
3. 确认 JSON 和 Markdown 都能生成
4. 再开始调类目、关键词、工作流
5. 最后再去改 prompt 或 schema

原因：

- 先验证“系统能跑”
- 再优化“结果够不够好”

如果一上来就同时改 prompt、代码、schema，很容易排错困难。

---

## 5. 如果要微调需求，应该改哪里

这个部分最重要。

### 场景 1：想改智能体的分析风格

比如你想让它：

- 更保守
- 更激进
- 更偏专利规避
- 更偏跨境物流友好

优先修改：

- [00_中枢调度主Agent_SystemPrompt.md](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/prompts/00_中枢调度主Agent_SystemPrompt.md)
- 对应子 Agent 的 prompt 文件

修改方法：

- 改“核心目标”
- 改“限制条件”
- 改“输出要求”

建议：

- 每次只改一个 Agent
- 改完先跑模拟版，再跑真实版

---

### 场景 2：想新增一个 Agent

比如你想加：

- 用户评论情绪 Agent
- 供应链可制造性 Agent
- 图片风格分析 Agent

需要改这几个地方：

1. 在 `prompts/` 下新增一个 prompt 文件  
2. 在 `config/agent_registry.yaml` 注册这个 Agent  
3. 在 `main.py` 里新增对应执行函数  
4. 把它插入对应 workflow 顺序里

推荐顺序：

- 先写 prompt
- 再加 registry
- 再加主程序逻辑

---

### 场景 3：想修改工作流顺序

比如你想把：

- 专利预警提前
- 场景拆解放到更后面
- 文档美化去掉

改这里：

- [agent_registry.yaml](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/config/agent_registry.yaml)

重点看：

- `routing_rules.workflow_1.order`
- `routing_rules.workflow_2.order`
- `routing_rules.workflow_3.order`
- `routing_rules.hybrid.order`

注意：

- 改顺序后，要确认 `main.py` 里对应 step 有逻辑支持

---

### 场景 4：想修改最终 JSON 输出字段

比如你想增加：

- 评分字段
- 优先级字段
- 类目字段
- 供应链建议字段

先改：

- [shared_data_contract.yaml](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/schemas/shared_data_contract.yaml)

再改：

- `main.py` 中构造 `idea` 和 `final_output` 的地方

原则：

- 先改 schema
- 再改代码
- 最后改 prompt

这样不容易字段对不上。

---

### 场景 5：想调 API 使用策略 / 控制成本

改这里：

- `.env`
- [api_clients.py](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/api_clients.py)
- [API使用策略与成本控制.md](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/API使用策略与成本控制.md)

你可以调整：

- `OPENAI_MODEL_NAME`
- 批量清洗优先用 DeepSeek 还是 Groq
- 最终报告是否一定走 GPT-4o

如果你的预算很敏感，最值得改的是：

- 让批量清洗尽量走 DeepSeek / Groq
- 只让最终终审和报告输出走 GPT-4o

---

### 场景 6：想调成本 / 物流规则

改这里：

- `main.py` 中的 `FBA_RULES_ESTIMATE`
- `infer_product_profile(...)`
- `estimate_fba_costs(...)`

如果你后面有更真实的数据，可以替换：

- FBA fee 表
- 头程单价
- 仓储单价
- 尺寸分层规则

---

### 场景 7：想调专利风险判断

改这里：

- `main.py` 中的
  - `run_ip_guard(...)`
  - `run_patent_risk_validation(...)`
  - `jaccard_similarity(...)`

你可以优化：

- 近似度阈值
- 检索 query 模板
- 风险等级判定规则

注意：

- 现在是“公开检索初筛”
- 不是律师级结论

---

### 场景 8：想改 Markdown 报告的样式

改这里：

- [06_文档美化Agent.md](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/prompts/06_文档美化Agent.md)
- `main.py` 中的 `render_markdown_report(...)`

如果你想加：

- 封面
- 目录
- 评分表
- 多方案对比表

优先改 `06_文档美化Agent.md`。

如果你想加一个“模型失败时也能固定输出”的硬模板，再去改 `render_markdown_report(...)` 里的 fallback。

---

## 6. 这几个文件是最常改的

如果你之后要维护项目，最常改的大概率是这几个：

- [main.py](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/main.py)
- [agent_registry.yaml](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/config/agent_registry.yaml)
- [shared_data_contract.yaml](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/schemas/shared_data_contract.yaml)
- [00_中枢调度主Agent_SystemPrompt.md](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/prompts/00_中枢调度主Agent_SystemPrompt.md)
- [06_文档美化Agent.md](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/prompts/06_文档美化Agent.md)
- [.env](C:/Users/Administrator/Documents/Codex/2026-06-10/files-mentioned-by-the-user-codex/outputs/Codex爆款产品灵感智能体资产包/.env)

---

## 7. 常见问题排查

### 1. 跑不起来，提示缺少 key

检查：

- `.env` 是否存在
- key 名是否正确
- 有没有多余空格

---

### 2. 搜索结果为空

检查：

- `SERPER_API_KEY` 或 `SERPAPI_API_KEY` 是否可用
- 当前网络是否能访问对应 API
- 搜索 query 是否太偏

---

### 3. 模型返回不是 JSON

这是大模型常见问题。

当前程序已经有 fallback，但如果你想更稳，可以：

- 缩短 prompt
- 强化“只输出 JSON”
- 降低返回字段复杂度

---

### 4. 结果太发散

优先调：

- `--categories`
- `--seed`
- 中枢 Agent prompt
- `shared_data_contract.yaml` 中的约束项

---

### 5. 成本看起来不准

正常。

因为当前是“公开规则 + 本地估算”。

要提高精度，你后面需要补：

- 真实包装尺寸
- 真实采购价
- 真实头程报价
- 真实 FBA fee 表

---

## 8. 推荐维护方式

建议你以后按下面的顺序维护：

1. 先改 prompt
2. 再改 registry
3. 再改 schema
4. 最后改主程序

这样风险最低，也最容易定位问题。

如果你要做较大改动，建议：

- 先跑 `local_agent_simulator.py`
- 再跑 `main.py`

---

## 9. 一句话总结

如果你只记住一句话：

`main.py` 是主入口，`prompts/` 决定智能体风格，`agent_registry.yaml` 决定工作流顺序，`shared_data_contract.yaml` 决定数据格式，`.env` 决定能不能真的调起外部能力。
