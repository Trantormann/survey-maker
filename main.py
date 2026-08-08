"""问卷星 Excel 回答模板与批量提交工具。"""

from __future__ import annotations

import argparse
import sys

import requests

from survey_maker import WorkbookValidationError, create_template, fetch_questions, read_answer_rows
from survey_maker.browser import BrowserPreparationError, SubmitProgress, batch_submit, prefilled_browser
from survey_maker.wjx import QuestionType, iter_choice_labels


def _add_url_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--url", required=True, help="已获授权的问卷星链接（wjx.cn 或 wjx.top）。")


def _non_negative_float(value: str) -> float:
    try:
        parsed = float(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("必须是非负数。") from error
    if parsed < 0:
        raise argparse.ArgumentTypeError("不能小于 0。")
    return parsed


def _load_questions(url: str):
    questions = fetch_questions(url)
    if not questions:
        raise ValueError("未在页面中解析到题目。问卷可能需要登录、已关闭，或使用了暂不支持的动态题型。")
    return questions


def _question_type_name(question_type: QuestionType) -> str:
    return {
        QuestionType.SINGLE_CHOICE: "单选",
        QuestionType.MULTIPLE_CHOICE: "多选",
        QuestionType.SELECT: "下拉选择",
        QuestionType.TEXT: "文本",
        QuestionType.UNSUPPORTED: "暂不支持",
    }[question_type]


_PROGRESS_STAGE_NAMES = {
    "starting": "开始",
    "opening": "打开页面",
    "filling": "填写",
    "filling_question": "填写题目",
    "submitting": "提交",
    "succeeded": "成功",
    "failed": "失败",
    "waiting": "等待",
    "stopped": "停止",
}


def _print_submit_progress(progress: SubmitProgress) -> None:
    stage_name = _PROGRESS_STAGE_NAMES.get(progress.stage, progress.stage)
    prefix = f"[{progress.position}/{progress.total} | Excel 第 {progress.excel_row} 行]"
    print(f"{prefix} {stage_name}：{progress.message}", flush=True)


def inspect_command(arguments: argparse.Namespace) -> int:
    questions = _load_questions(arguments.url)
    print(f"已解析 {len(questions)} 道题：")
    for question in questions:
        required = "必填" if question.required else "选填"
        print(f"Q{question.number} [{_question_type_name(question.question_type)}，{required}] {question.title}")
        for index, label in enumerate(iter_choice_labels(question), start=1):
            print(f"  {index}. {label}")
    return 0


def template_command(arguments: argparse.Namespace) -> int:
    questions = _load_questions(arguments.url)
    output_path = create_template(questions, arguments.output)
    print(f"已生成 {len(questions)} 列的 Excel 模板：{output_path}")
    print("请在 answers 工作表从第 2 行开始填写；每一行是一份完整问卷答案。")
    return 0


def validate_command(arguments: argparse.Namespace) -> int:
    questions = _load_questions(arguments.url)
    answer_rows = read_answer_rows(arguments.excel, questions)
    if answer_rows:
        row_numbers = "、".join(str(row.excel_row) for row in answer_rows)
        print(f"Excel 校验通过：共 {len(answer_rows)} 行，Excel 行号为 {row_numbers}。")
    else:
        print("Excel 格式校验通过，但 answers 工作表没有有效答案行。")
    return 0


def prepare_command(arguments: argparse.Namespace) -> int:
    if not arguments.authorized:
        raise PermissionError("预填前必须添加 --authorized，确认你拥有该问卷的测试或填写授权。")

    questions = _load_questions(arguments.url)
    answer_rows = read_answer_rows(arguments.excel, questions, allow_manual_questions=True)
    answer_row = next((row for row in answer_rows if row.excel_row == arguments.row), None)
    if answer_row is None:
        raise WorkbookValidationError(f"未找到 Excel 第 {arguments.row} 行的有效答案。")

    with prefilled_browser(arguments.url, answer_row):
        print(f"已预填 Excel 第 {answer_row.excel_row} 行。")
        print("请在浏览器中逐题核对。此工具不会点击提交、处理验证码或记录提交状态。")
        input("核对完成后，按 Enter 关闭浏览器：")
    return 0


def submit_command(arguments: argparse.Namespace) -> int:
    if not arguments.authorized:
        raise PermissionError("提交前必须添加 --authorized，确认你拥有该问卷的测试或填写授权。")

    questions = _load_questions(arguments.url)
    answer_rows = read_answer_rows(arguments.excel, questions)
    if not answer_rows:
        raise WorkbookValidationError("Excel 中没有可提交的答案行。")

    total = len(answer_rows)
    if arguments.rows is None:
        selected = answer_rows
    else:
        requested_rows = set(arguments.rows)
        available_rows = {row.excel_row for row in answer_rows}
        missing_rows = sorted(requested_rows - available_rows)
        if missing_rows:
            missing = "、".join(str(row) for row in missing_rows)
            raise WorkbookValidationError(f"未找到指定的 Excel 行号：{missing}。本次不会提交任何行。")
        selected = [row for row in answer_rows if row.excel_row in requested_rows]

    print(
        f"准备批量提交 {len(selected)} / {total} 行答案"
        f"（headless={arguments.headless}，间隔={arguments.delay}s，抖动={arguments.jitter}s，speed={arguments.speed}）……",
        flush=True,
    )
    results = batch_submit(
        arguments.url,
        selected,
        headless=arguments.headless,
        delay=arguments.delay,
        jitter=arguments.jitter,
        speed=arguments.speed,
        progress_callback=_print_submit_progress,
    )

    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]

    print()
    print(f"完成：{len(succeeded)} 成功，{len(failed)} 失败，共 {len(results)} 行。")
    if failed:
        failed_rows = "、".join(str(result.excel_row) for result in failed)
        print(f"失败的 Excel 行：{failed_rows}。")
    if len(results) < len(selected):
        print(f"检测到平台安全或频率保护，已停止；剩余 {len(selected) - len(results)} 行未尝试。")
    if failed:
        return 1
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将已获授权的问卷星问卷解析为 Excel 模板，并支持人工预填或批量自动提交。"
    )
    commands = parser.add_subparsers(dest="command", required=True)

    # inspect
    inspect_parser = commands.add_parser("inspect", help="读取链接并列出题目和选项。")
    _add_url_argument(inspect_parser)
    inspect_parser.set_defaults(handler=inspect_command)

    # template
    template_parser = commands.add_parser("template", help="从问卷生成 Excel 回答模板。")
    _add_url_argument(template_parser)
    template_parser.add_argument("--output", required=True, help="要生成的 .xlsx 文件路径。")
    template_parser.set_defaults(handler=template_command)

    # validate
    validate_parser = commands.add_parser("validate", help="校验 Excel 的每一份答案是否可自动提交。")
    _add_url_argument(validate_parser)
    validate_parser.add_argument("--excel", required=True, help="填写后的 .xlsx 文件路径。")
    validate_parser.set_defaults(handler=validate_command)

    # prepare（原有人工确认）
    prepare_parser = commands.add_parser("prepare", help="在可见浏览器中预填一行答案，等待人工核对。")
    _add_url_argument(prepare_parser)
    prepare_parser.add_argument("--excel", required=True, help="填写后的 .xlsx 文件路径。")
    prepare_parser.add_argument("--row", required=True, type=int, help="answers 工作表中要预填的 Excel 行号。")
    prepare_parser.add_argument(
        "--authorized", action="store_true", help="确认你拥有该问卷的测试或填写授权。"
    )
    prepare_parser.set_defaults(handler=prepare_command)

    # submit（批量自动提交）
    submit_parser = commands.add_parser("submit", help="批量自动提交 Excel 中的所有答案行。")
    _add_url_argument(submit_parser)
    submit_parser.add_argument("--excel", required=True, help="填写后的 .xlsx 文件路径。")
    submit_parser.add_argument(
        "--row", dest="rows", action="append", type=int,
        help="仅提交指定行号（可多次使用）；省略则提交全部行。",
    )
    submit_parser.add_argument(
        "--headless", action="store_true", default=True,
        help="无头模式运行浏览器（默认开启）。",
    )
    submit_parser.add_argument(
        "--no-headless", dest="headless", action="store_false",
        help="以可见浏览器运行（用于调试）。",
    )
    submit_parser.add_argument(
        "--delay", type=_non_negative_float, default=2.0,
        help="每次提交之间的基础间隔秒数（默认 2 秒）。建议 10 秒以上以降低触发频率保护的风险。",
    )
    submit_parser.add_argument(
        "--jitter", type=_non_negative_float, default=0.0,
        help="在基础间隔上附加的随机抖动上限秒数（默认 0）。例如 --delay 15 --jitter 10 "
        "表示每次等待 15–25 秒随机时长，使提交节奏更接近真人。",
    )
    submit_parser.add_argument(
        "--speed", choices=["fast", "human"], default="fast",
        help="行为模拟模式：fast=极速直填（默认），human=贝塞尔鼠标+逐字输入+随机停顿（更慢但更像真人）。",
    )
    submit_parser.add_argument(
        "--authorized", action="store_true", help="确认你拥有该问卷的测试或填写授权。"
    )
    submit_parser.set_defaults(handler=submit_command)

    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    try:
        return arguments.handler(arguments)
    except (BrowserPreparationError, PermissionError, ValueError, WorkbookValidationError, requests.RequestException) as error:
        print(f"错误：{error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\n已取消。", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
