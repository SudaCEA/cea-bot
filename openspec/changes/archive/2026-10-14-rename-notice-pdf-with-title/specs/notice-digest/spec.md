# notice-digest Specification (delta)

## ADDED Requirements

### Requirement: PDF 产物命名

业务层 SHALL 以 `YYMMDD-<通知标题>` 的形式命名生成的 PDF 产物（扩展名 `.pdf`），
其中 `YYMMDD` 取自该通知的发布日期。标题中的文件系统非法字符（含 `< > : " / \ | ? *`、
控制字符，以及结尾的点与空格）SHALL 被剔除，中文 SHALL 被保留；
标题部分 SHALL 截断到不超过 80 个字符以保证路径可用；净化后标题为空时 SHALL 使用固定占位
`未命名`。发布日期缺失或非 `YYYY-MM-DD` 形式时，命名 SHALL 以固定占位 `000000` 开头，
SHALL NOT 留空，也 SHALL NOT 因此中断推送。同日出现同名产物时，
覆盖已存在文件之前 SHALL 记录告警，SHALL NOT 静默覆盖。

#### Scenario: 正常命名
- **WHEN** 处理发布日期为 `2026-10-01`、标题为"通知标题"的通知
- **THEN** 产物名为 `261001-通知标题.pdf`

#### Scenario: 标题含非法字符
- **WHEN** 标题为"关于XX/YY的通知"（含 `/`）
- **THEN** 非法字符被剔除，产物名为 `261001-关于XXYY的通知.pdf`

#### Scenario: 标题过长
- **WHEN** 标题长度超过 80 个字符
- **THEN** 标题部分被截断到 80 个字符，产物名仍可用

#### Scenario: 发布日期缺失
- **WHEN** 通知缺少发布日期
- **THEN** 产物名以 `000000-` 开头，推送流程继续，不因此中断

#### Scenario: 同日同标题重名
- **WHEN** 同一发布日期下出现标题相同的两条通知
- **THEN** 两条产物名相同；覆盖已存在文件时记录告警
