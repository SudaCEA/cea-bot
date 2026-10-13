# tests/fixtures —— 通知抓取的真实字节样本

这里的两个文件是**站点返回的原始字节**（`Content-Type` 与响应体均未做任何转码预处理），
供 `tests/test_notice_parse.py` 做离线解析测试。**禁止**用转码后的文本替换它们：
编码自适配（D10）正是靠"声明与实际不符"的原始字节才测得出来。

| 文件 | 来源 URL | 字节数 | 抓取时间 |
|---|---|---|---|
| `notice_list_type1.bin` | `http://file.suda.edu.cn/defaultroot/SInformAction.do?action=init&type=1` | 24217 | 2026-10-13 14:36 (+08:00) |
| `notice_detail_40773772.bin` | `http://file.suda.edu.cn/defaultroot/gov/info_view_my.jsp?whir_new_verifyCode=1&editId=40773772` | 24758 | 2026-10-13 14:36 (+08:00) |

抓取方式：普通 HTTP GET（`.venv\Scripts\python` + 标准库 `urllib.request`，浏览器 UA），
不带 Cookie、不做登录。这两个页面都是服务端渲染，一次 GET 即得完整 HTML。

## 列表页样本（`notice_list_type1.bin`）

- 响应头：`Content-Type: text/html;charset=UTF-8`
- 页面内 `<meta>`：`charset=gb2312`
- **实际字节：UTF-8**（`raw.decode("gb2312")` 在第 106 字节就抛 `UnicodeDecodeError`，
  `gb18030` 在第 1133 字节抛错，`utf-8` 严格解码成功）

这正是需求「编码自适配」里"声明与实际不符"的场景：按声明解会失败（或解出乱码），
必须回退到 UTF-8，且回退后要能解出 `学生标兵` 这样的正确中文。
`tests/test_notice_parse.py::test_list_fixture_is_really_the_encoding_trap`
会反向确认样本仍然保持这个现场——样本一旦被换成"声明与实际一致"的页面，那组用例就会失效。

结构事实（解析器依赖的选择器）：

- 每页 20 条，行结构为 3 个 `<td>`：发布日期 / 标题（含 `<a>`）/ 发布单位
- 稳定标识在行内锚点的 `onclick` 里：`open_tz('40773772')`（实测首条为 `40773772`）
- 锚点没有 `href`，详情页 URL 必须由业务层按 `editId` 拼出
- 页面里另有栏目导航行与搜索表单行也是 `<tr>`，它们**没有** `open_tz`

## 详情页样本（`notice_detail_40773772.bin`）

- 响应头与 `<meta>` 均为 `charset=utf-8`，字节就是 UTF-8（无编码陷阱）
- 标题：`<strong id="title" style="font-family:宋体;font-size:36px; ...">`
- 正文：`<td ... id="content" class="_c">`（Word 粘贴的富文本：`<span style="font-family: 仿宋; font-size: 16pt;">`，
  含未闭合的 `<p>`/`<span>`，故解析器只按**同名标签**配对判断正文区域结束，不用全局深度）
- 文号是正文首段：`学字﹝2026﹞63号`
- 附件清单在两个 hidden input 里（`|` 分隔、末尾多一个空段）：
  `infoPicName`（展示名 3 项）、`infoPicSaveName`（保存名 3 项、同序）
- 页面里**没有** `editId`，详情标识只能由列表页带来

## 重新抓取

站点内容会变（列表首页每天都有新通知），重新抓取后请同步更新上表的**字节数与抓取日期**，
并跑一遍 `.venv\Scripts\pytest tests/test_notice_parse.py -q`：
用例里断言了首条标题/日期/文号与附件名，样本换了内容就要同步更新这些期望值。
