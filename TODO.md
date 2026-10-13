# TODO

## 文件传输需求 → 已实现并归档（add-file-transfer，2026-09-23）

- [x] 文件类消息收发：`OutMessage.file`（text/image/file 三选一）出站、`Message.files` 入站引用
- [x] 文件下载：`bot.fetch_file(ref)`（get_file → base64 优先 / http(s) URL 降级；16MB 上限）
- [x] 群文件独立上传：已移除（file 消息本身会同步出现在群文件列表，无增益）

已知限制与后续选项：

- 分端部署（bot 与 NapCat 不同机）时 `get_file` 可能仅返回 NapCat 本地路径 → 明确报错，
  不做跨机取回；如需跨机取文件，二期可接 NapCat Stream API（`download_file_stream`）
- 入站图片 URL 约 2 小时过期（`get_image` / `nc_get_rkey` 可刷新）
