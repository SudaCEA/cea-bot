# -*- coding: utf-8 -*-
"""通知推送（notice-digest）的业务领域层：数据模型、编码解码与列表/详情解析。

分层与归属（对应 openspec/changes/add-notice-digest/design.md）：

- `models.py`：纯数据（`NoticeBrief` / `NoticeDetail` / `Attachment`），不含任何逻辑；
- `parse.py`：**纯函数**解码与解析层，原始字节 → 可信文本 → 结构化数据，不碰网络与磁盘；
- 抓取（`fetch.py`）、增量状态（`schema.py`）、摘要（`summarize.py`）、PDF（`pdf.py`）
  由后续任务落地，IO 一律留在各自的边界模块里。

依赖方向：本包属业务层，只依赖标准库与既有依赖，**不 import 平台 SDK，也不 import `cea` 框架**
（框架是平台无关的消息管道，反向依赖会让解析层被拖进运行时的装配细节）。
"""
