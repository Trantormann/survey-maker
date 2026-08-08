# Survey Maker

该工具将已获授权的问卷星链接解析为 Excel 回答模板，并把指定的一行答案预填到一个可见浏览器中，供人工逐题核验。

它仅支持 `wjx.cn` 和 `wjx.top` 链接，以及单选、多选、下拉选择和文本题。它不会自动点击提交、处理验证码、规避平台控制或批量提交问卷。

## 安装

```powershell
python -m pip install -r requirements.txt
python -m playwright install chromium
```

## 使用

先查看脚本解析到的题目和选项：

```powershell
python main.py inspect --url "https://v.wjx.cn/vm/your-form.aspx"
```

生成 Excel 模板：

```powershell
python main.py template --url "https://v.wjx.cn/vm/your-form.aspx" --output "answers.xlsx"
```

填写 `answers.xlsx` 后校验全部数据行：

```powershell
python main.py validate --url "https://v.wjx.cn/vm/your-form.aspx" --excel "answers.xlsx"
```

预填 Excel 第 2 行并在浏览器中人工核验：

```powershell
python main.py prepare --url "https://v.wjx.cn/vm/your-form.aspx" --excel "answers.xlsx" --row 2 --authorized
```

`prepare` 打开的是可见浏览器。脚本只负责预填，不会提交；请人工核对每一题，并自行决定是否提交。关闭提示后浏览器会退出。

## Excel 格式

- `answers` 工作表第 1 行由脚本生成，必须保留为 `Q1`、`Q2`、`Q3`……，顺序与问卷题号一致。
- 从第 2 行开始，每一行代表一份完整问卷答案；每一列对应同序号的一道题。
- 单选和下拉题可填写完整选项文字、选项序号（从 1 开始）或选项值。
- 多选题用英文分号 `;` 或中文分号 `；` 分隔多个选项，例如 `技术；设计`。
- 文本题直接填写希望出现在输入框中的内容。
- `question_guide` 工作表包含每道题的题干、题型、必填状态和可选答案。不要在这里录入作答数据。

每次预填前都会重新读取当前问卷并校验 Excel。如果问卷题目被修改，先重新生成模板，避免错位填写。