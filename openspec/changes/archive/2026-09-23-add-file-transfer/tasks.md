# add-file-transfer 任务清单

## 1. 模型层（cea/types.py）

- [x] 1.1 新增 `FileRef(file_id, name="", size=0, url=None)` 与 `FileData(data, name, size)` frozen dataclass；`Message` 增加 `files: tuple[FileRef, ...] = ()`
- [x] 1.2 `OutMessage` 增加 `file: str | None` 与 `file_name: str = ""`，校验升级为 text/image/file 三选一互斥；docstring 更新
- [x] 1.3 模型单测：三选一互斥（两两组合拒绝）、恰好一项通过、`cea/__init__.py` 导出更新

## 2. 能力句柄（cea/bot.py + cea/app.py）

- [x] 2.1 `bot.py`：`MAX_FILE_BYTES = 16MB` 常量、`FileTooLargeError`；`Bot` 增加可选注入字段与 `fetch_file(ref)` 方法（无注入抛 `NotImplementedError`，Bot 侧做 16MB 预检）；原计划的 `upload_group_file` 已废止移除（见 design D5）
- [x] 2.2 `app.py`：装配时 `getattr` 探测 adapter 的 `fetch_file` 并注入 Bot；单测：无注入抛错、有注入转发、超限拒绝

## 3. NapCat 前端（cea/adapters/napcat.py）

- [x] 3.1 `normalize_event`：file 段收集为 `FileRef`（file_id 必备，name/file_size/url 尽力填充）
- [x] 3.2 `build_segments`：file 源 → `file` 段（`name` 透传）
- [x] 3.3 `fetch_file`：`get_file` → base64 优先 / http(s) url 降级（session 下载，流式计数）→ `FileData`；非 http url（NapCat 本地路径）明确报错（分端限制，真机实证）；16MB 超限抛 `FileTooLargeError`；aiohttp 异常包装为 `RuntimeError`
- [x] 3.4 （已废止）`upload_group_file` 移除：file 消息本身会出现在群文件列表，独立上传无增益（真机观察后按用户决策撤除）
- [x] 3.5 单测：入站收集（有/无 file_id）、段构造（含 name）、fake call 下载（base64 命中 / url 降级 / 本地路径报错 / 超限）

## 4. QQ 官方前端（cea/adapters/qqbotpy.py）

- [x] 4.1 `send()` 入口判 `msg.file` → `log.warning("官方平台不支持文件消息，丢弃")` + return；单测验证不触达 fake api

## 5. 回归与真机

- [x] 5.1 全量 pytest + mypy 零错误（含 handlers）
- [x] 5.2 真机冒烟：file 段出站（URL 与 base64 源）实测通过；fetch 下载的分端限制为预期行为（明确报错，单测覆盖），不再真机复验
- [x] 5.3 `TODO.md` 清理文件传输条目；`handlers/basic.py` 临时调试命令已全部移除
