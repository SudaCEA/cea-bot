# add-napcat-adapter 任务清单

## 1. 配置与装配

- [x] 1.1 `config.example.yaml` / `config.yaml` 增加 `adapter` 选择项与 napcat 参数（`ws_url` / `token`），注释写明 NapCat 侧 WS 服务端配置要点（host/port、token、`messagePostFormat: array`）
- [x] 1.2 `main.py` 增加 `build_adapter(cfg)` 装配工厂：缺省 `qqofficial`、`napcat` 分支校验 `ws_url` 必填与 `token` 可选、未知值快速失败提示可选值；凭据校验随分支走
- [x] 1.3 `cea/types.py` 的 `OutMessage` docstring 补充 image 语义说明（URL/本地路径/base64，形态随 adapter 能力）

## 2. NapCat 适配器

- [x] 2.1 `cea/adapters/napcat.py` 纯函数 `normalize_event`：元事件（心跳/生命周期）返回 None；`message.group`/`message.private` → 统一 `Message`（kind 映射、QQ 号字符串、群名片优先、text 段拼接、images URL、self_id 判 at_me、reply 段取 reply_to、raw 透传）
- [x] 2.2 纯函数 `build_segments`：`OutMessage` → 段数组（reply 段置首、at 逐个成段、text/image 三态 image 源）
- [x] 2.3 OneBot WS 客户端：aiohttp `ws_connect` + `Authorization: Bearer` 鉴权（注释注明 query 参数回退）、自增 echo + Future 表、`call(action, params, timeout=30)`、读循环区分事件帧与响应帧
- [x] 2.4 `NapCatAdapter.run()`：重连循环（5 秒间隔、stopping 置位退出）+ 事件归一化投递 inbound + `pump_outbound` 消费 outbound
- [x] 2.5 `NapCatAdapter.send()`：无连接时等待（stopping / 连接就绪，超时记日志丢弃）→ `build_segments` → 按 kind 路由 `send_group_msg` / `send_private_msg`；kind=channel 拒绝记日志；status!=ok 与异常一律记日志不外抛
- [x] 2.6 模块 docstring：接入前提（NapCat 配置）、风控提示（真实 QQ 号、需限流自行实现）、跨机部署本地路径限制说明

## 3. 测试

- [x] 3.1 归一化单测：群文本、@ 判定、图片 URL、引用回复、心跳忽略、未建模段 raw 透出、私聊映射
- [x] 3.2 段构造单测：纯文本、被动回复置首、at 段、URL/本地路径/base64 图片源
- [x] 3.3 装配选择测试：napcat 分支、缺省 qqofficial、未知值报错
- [x] 3.4 全量单测回归 + mypy 零错误（含新增文件）

## 4. 真机验证与收尾

- [x] 4.1 NapCat 侧新建 WS 服务端（WebUI：host/port、启用 token、array 格式），`config.yaml` 填入 napcat 参数
- [x] 4.2 真机冒烟：`ping` 命令收发、图片收发（URL 与本地路径）、被动回复、主动消息、断线重连观察
- [x] 4.3 `main.py` docstring 更新运行说明（NapCat 前置步骤），清理临时调试产物
