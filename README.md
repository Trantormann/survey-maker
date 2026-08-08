# Survey Maker

将问卷星链接解析为 Excel 回答模板，再通过预填 Excel 进行批量问卷提交。

目前仅支持 `wjx.cn` 和 `wjx.top` 链接，以及单选、多选、下拉选择和文本题。

*工具不会处理验证码或规避平台限流；检测到验证码或频率/安全拦截后，会停止后续批处理。请在合法授权范围内使用。*

## 安装

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 命令一览

| 命令 | 用途 |
|------|------|
| `inspect` | 解析问卷链接，列出题目和选项 |
| `template` | 生成 Excel 回答模板（含 answers 和 question_guide 两个工作表）|
| `validate` | 严格校验 Excel 中每一行答案是否可自动提交 |
| `prepare` | 在可见浏览器中预填单行答案，等待人工核对（不自动提交）|
| `submit` | 批量自动提交 Excel 中的答案行 |

## 使用

### 1、查看题目

```powershell
python main.py inspect --url "https://v.wjx.cn/vm/your-form.aspx"
```

### 2、生成 Excel 模板

```powershell
python main.py template --url "https://v.wjx.cn/vm/your-form.aspx" --output "answers.xlsx"
```

### 3、校验答案

```powershell
python main.py validate --url "https://v.wjx.cn/vm/your-form.aspx" --excel "answers.xlsx"
```

`validate` 采用自动提交的严格规则；必填的暂不支持题型会直接报错，避免未填写完整的答卷进入批处理。

### 4、批量提交

```powershell
python main.py submit --url "https://v.wjx.cn/vm/your-form.aspx" --excel "answers.xlsx" --authorized
```

## 其他操作

### prepare — 人工预填单行

```powershell
python main.py prepare --url "https://v.wjx.cn/vm/your-form.aspx" --excel "answers.xlsx" --row 2 --authorized
```

打开可见浏览器，预填第 2 行答案。脚本只负责预填，不会点击提交；请人工逐题核对后自行决定是否提交。必填的暂不支持题型可保持 Excel 单元格为空，并在此浏览器中手动填写。关闭提示后浏览器会退出。

### submit — 批量自动提交

**提交全部行：**

```powershell
python main.py submit --url "https://v.wjx.cn/vm/your-form.aspx" --excel "answers.xlsx" --authorized
```

**仅提交指定行：**

```powershell
python main.py submit --url "https://v.wjx.cn/vm/your-form.aspx" --excel "answers.xlsx" --row 2 --row 5 --authorized
```

**以可见浏览器调试运行：**

```powershell
python main.py submit --url "https://v.wjx.cn/vm/your-form.aspx" --excel "answers.xlsx" --no-headless --authorized
```

**自定义提交间隔（默认 2 秒）：**

```powershell
python main.py submit --url "https://v.wjx.cn/vm/your-form.aspx" --excel "answers.xlsx" --delay 5 --authorized
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `--url` | 问卷星链接（必填）| — |
| `--excel` | 填写后的 .xlsx 文件路径（必填）| — |
| `--row` | 仅提交指定行号，可多次使用；省略则提交全部 | 全部行 |
| `--headless` | 无头模式运行浏览器 | 开启 |
| `--no-headless` | 可见浏览器运行（调试用）| — |
| `--delay` | 每次提交之间的基础间隔秒数 | 2.0 |
| `--jitter` | 在基础间隔上附加的随机抖动上限秒数 | 0.0 |
| `--authorized` | 确认你拥有该问卷的测试或填写授权（必填）| — |

**提交后行为：**

- 依次为每行答案打开一个独立的浏览器上下文，填写并点击提交
- 启动浏览器前先校验全部 Excel 行；任一行无效时不会开始批处理
- 指定 `--row` 时，只要有任一行号不存在，本次不会提交任何行
- 提交后检测页面是否存在成功标记（如"答卷成功"）、错误提示或验证码拦截
- 每行实时显示总进度、Excel 行号和阶段：开始、打开页面、填写的具体题号、提交、成功、失败或等待下一行
- 失败时立即显示该行的错误原因；结束时输出成功、失败和未尝试行的汇总
- 遇到验证码、频率或安全拦截时，当前行标记为失败并停止后续行，需人工处理
- 全部成功返回退出码 0，有失败返回 1

**降低触发频率保护的建议：**

- 使用较大的 `--delay`（如 15 秒以上）并配合 `--jitter` 添加随机抖动，例如 `--delay 15 --jitter 10` 表示每次等待 15–25 秒随机时长
- 用 `--no-headless` 以可见浏览器运行，行为特征更接近真人操作
- 用 `--row` 将大批量拆分为小批次，每批之间手动等待一段时间再继续
- 工具不会绕过验证码或安全机制；触发保护后需人工处理

## Excel 格式

**`answers` 工作表：**

- 第 1 行由脚本生成，必须保留为 `Q1`、`Q2`、`Q3`……顺序与问卷题号一致
- 从第 2 行开始，每一行代表一份完整问卷答案
- 每一列对应同序号的一道题

**填写规则：**

| 题型 | 填写方式 |
|------|----------|
| 单选 / 下拉选择 | 仅填写一个选项的完整文字、选项序号（从 1 开始）或选项值 |
| 多选 | 多个选项用英文分号 `;` 或中文分号 `；` 分隔，如 `技术；设计`；脚本会校验题目规定的最少/最多选择数，并确认页面中每个 checkbox 的最终状态 |
| 文本 | 直接填写希望出现在输入框中的内容 |
| 暂不支持的题型 | 不能自动提交；可使用 `prepare` 打开浏览器后手动填写 |

若提示“是单选题，却填写了多个有效选项”，请查看 `question_guide` 工作表中的题型，只保留一个答案。只有标记为“多选”的题目可以用分号填写多个选项；若问卷本身需要允许多选，应先在问卷星中将该题配置为多选，再重新生成模板。

**`question_guide` 工作表：**

包含每道题的题干、题型、必填状态、可选答案和填写规则。不要在这里录入作答数据。

> **提示：** 每次预填或提交前都会重新读取当前问卷并校验 Excel。如果问卷题目被修改，请先重新生成模板再填写，避免错位。

## 项目结构

```
survey-maker/
├── main.py                  # CLI 入口（5 个子命令）
├── requirements.txt         # 依赖：openpyxl, playwright, requests
├── survey_maker/
│   ├── __init__.py          # 公共接口导出
│   ├── wjx.py               # 问卷星 HTML 解析（标准库 HTMLParser，无第三方依赖）
│   ├── excel.py             # Excel 模板生成 + 逐行答案校验（openpyxl）
│   └── browser.py           # Playwright 浏览器预填 + 批量自动提交
└── tests/
    ├── test_wjx.py          # HTML 解析测试
    ├── test_excel.py        # Excel 模板与校验测试
    └── test_browser.py      # 浏览器预填测试（需 Playwright Chromium）
```

## 依赖

- `openpyxl >= 3.1, < 4` — Excel 读写
- `playwright >= 1.50, < 2` — 浏览器自动化
- `requests >= 2.31, < 3` — 问卷页面抓取

## 运行测试

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

浏览器测试需要 Playwright Chromium 已安装。未安装时会自动跳过。
