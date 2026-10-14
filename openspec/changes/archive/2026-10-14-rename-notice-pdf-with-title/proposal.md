# Proposal: rename-notice-pdf-with-title

## Why

PDF 产物现在叫 `<日期>-<editId>.pdf`（如 `2026-10-13-40775683.pdf`）。`data/notices/` 是给人看的归档目录，
但文件名里只有站点内部 ID —— 打开目录无法判断哪份是哪条通知，标题明明就在正文与状态库里，却要反查才能对上。

真实使用里已经撞到这个痛点：一次定点推送 3 条通知后，归档目录里躺着 3 个纯数字文件名，无法一眼分辨。

## What Changes

- PDF 产物名改为 **`YYMMDD-<通知标题>.pdf`**（例：`261001-通知标题.pdf`）
- 净化规则从"只保留 ASCII 字母数字"改为"剔除文件系统非法字符"，使**中文标题得以保留**
- 标题长度加上限（保证 Windows 路径可用）；净化后为空时用固定占位
- 发布日期缺失时用 `000000` 占位，保持格式一致（原为中文占位"未知日期"）
- 同日同标题重名时**覆盖前记 WARNING**，不做静默覆盖

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `notice-digest`：**ADDED** 一条需求「PDF 产物命名」。主规格此前完全没有约定产物名，
  本次把"给人看的归档名"变成有约束的契约（含重名告警的场景）。

## Impact

- **代码**：`src/cea_bot/notices/pdf.py`（`pdf_file_name` 签名与净化规则）、
  `src/cea_bot/handlers/notice.py`（调用点改传标题 + 重名告警）
- **测试**：`tests/test_notice_pdf.py`（现有 3 条断言按新规则重写，并补中文保留 / 非法字符 /
  长度截断 / 日期缺失 / 标题为空等用例）；`tests/test_notice_handler.py` 若依赖旧名需同步
- **文档**：`README.md` 通知推送一节补一句产物命名规则（便于用户看 `data/notices/`）；
  `config.example.yaml` 里那条写着旧命名 `<日期>-<editId>.pdf` 的注释同步更正
  （**纯注释**，键名/位置/键集合不变 —— 留着它就与主规格直接矛盾）
- **兼容性**：**不迁移历史产物** —— 归档里旧名文件保持原样，状态库里旧的 `pdf_path` 不回填
- **风险**：① 去掉 `editId` 后同日同标题会撞名 → 见 design D4（记 WARNING，不静默覆盖）；
  ② 标题含 `/`、`:`、`*` 等字符在 Windows 上非法 → design D2 净化；
  ③ 标题过长可能触发 Windows MAX_PATH → design D3 截断
