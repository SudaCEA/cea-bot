# add-group-whitelist 设计

## Context

分发链路：`adapter → inbound → App._dispatch`（spawn on_message ×N + command 匹配）。过滤需求点在业务层拿到消息**之前**——否则 echo 等处理器已触发、`_last_chat` 类状态已污染。配置通路现成：`main.py` 的 `check_config` 已按 adapter 分支校验。

## Goals / Non-Goals

**Goals:**

- 非白名单群的消息在分发前静默丢弃（debug 日志）
- 空白名单 = 不过滤（向后兼容，零配置用户无感）
- 仅约束 kind=group；配置走既有 config.yaml 通路

**Non-Goals:**

- 出站侧过滤（handler 不该被触发后才拦）
- 私聊黑/白名单、按用户过滤
- 运行时热更新白名单（改配置重启生效）

## Decisions

**D1. 过滤点在 `App._dispatch` 取到消息后、spawn 前。** 消息"对业务层不存在"：handler 不触发、业务状态不更新。备选：adapter 层过滤（污染前端职责——过滤是框架策略不是平台能力）；outbound 层过滤（handler 已执行，浪费且有副作用）。

**D2. 白名单以构造参数注入 `App`。** `App(adapter, group_whitelist: Collection[str] | None = None)`，内部归一化为 `frozenset[str]`；None/空集 → 关闭过滤。App 保持平台无关（不过滤消息模型以外的东西）。

**D3. 匹配用 `msg.chat.id` 字符串精确比对。** 白名单配置为字符串列表；QQ 号/群号在统一模型中本就是字符串（int→str 已在前端边界完成），不做数值容错。

**D4. 丢弃时 `log.debug`（不打 info）。** 被过滤群通常是高频闲聊群，info 会刷屏；需要排查时调日志级别。

**D5. 配置解析进 `check_config`：`group_whitelist` 接受 list，逐项 str+strip，缺省 `[]`；非法元素（非字符串数字）保留原样转 str，不做硬校验（群号格式平台各异）。** `config.example.yaml` 注释给出语义（空=不过滤）。

## Risks / Trade-offs

- [空白名单语义误读（用户以为空=全拒）] → config 注释与 spec scenario 双重写明"空=不过滤"
- [群号抄错导致"机器人不理人"难排查] → debug 日志含 chat.id；文档提示先看日志
- [与文件冒烟交互] → 冒烟群须在白名单内（config.yaml 实装时预填测试群号 348909747）

## Migration Plan

无迁移。默认不配置 = 行为不变；实装时在 `config.yaml` 预填测试群号。回滚 = 删除配置项即可。

## Open Questions

（无——三个语义假设已作为默认写入 spec：空=不过滤、仅群聊、debug 日志；用户可纠正）

