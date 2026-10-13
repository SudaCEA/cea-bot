# add-group-whitelist 提案

## Why

机器人将部署到真实社群，需要限制响应范围：只对指定群做出响应，避免在无关群里被打扰式触发（echo 等处理器会对每条消息回复）。当前分发器对所有来源消息一律分发，没有来源过滤机制。

## What Changes

- **分发层（cea/app.py）**：`App` 新增可选 `group_whitelist` 参数（群号集合）；分发器在 spawn 任何 handler 之前丢弃来源不在白名单内的群消息（记 debug 日志）——被过滤消息不触发命令、不触发消息处理器、不更新业务侧状态
- **配置通路（main.py + config）**：`check_config` 解析 `group_whitelist`（字符串列表，缺省空）并传入装配；`config.example.yaml` / `config.yaml` 增加配置项与注释
- **语义（探索期假设，可纠正）**：白名单为空/未配置 = 不过滤（兼容现状）；仅约束 `kind=group`，私聊/其他会话类型不受限；定时任务与主动消息不受影响（白名单只过滤入站分发）

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `dispatching`：新增「群聊白名单过滤」要求——白名单内群消息正常分发；非白名单群消息在分发前被丢弃且不进入业务层；空白名单不过滤；私聊不受限

## Impact

- **代码**：`cea/app.py`（构造参数 + `_dispatch` 过滤门）、`main.py`（配置解析与传参）、`config.example.yaml` / `config.yaml`、`tests/`（过滤/放行/空白名单/私聊不受限 4 例）
- **依赖**：无新增
- **spec**：`dispatching` 追加 1 条 requirement
- **风险**：低——纯分发侧前置过滤，不触碰模型与前端；被过滤行为对 handler 透明（消息"看起来没来过"）
