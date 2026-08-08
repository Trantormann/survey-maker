"""在浏览器中预填与批量提交问卷星答案。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import time
from typing import Callable, Iterator, Sequence

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Locator, Page, sync_playwright

from .excel import AnswerRow
from .wjx import Choice, Question, QuestionType


class BrowserPreparationError(RuntimeError):
    """无法加载、预填或提交问卷页面时抛出。"""


# ---------------------------------------------------------------------------
# 轻量结果记录
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SubmitResult:
    excel_row: int
    success: bool
    message: str


@dataclass(frozen=True)
class SubmitProgress:
    position: int
    total: int
    excel_row: int
    stage: str
    message: str


ProgressCallback = Callable[[SubmitProgress], None]


# ---------------------------------------------------------------------------
# 单行预填 + 人工确认（原有功能，保持兼容）
# ---------------------------------------------------------------------------

@contextmanager
def prefilled_browser(url: str, answer_row: AnswerRow) -> Iterator[Page]:
    """打开问卷、填入一行答案，并保持浏览器直到调用方结束上下文。"""
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=False)
        except PlaywrightError as error:
            raise BrowserPreparationError(
                "未找到 Playwright Chromium。请运行：python -m playwright install chromium"
            ) from error

        context = browser.new_context()
        page = context.new_page()
        try:
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30_000)
                _fill_answer_row(page, answer_row)
            except PlaywrightError as error:
                raise BrowserPreparationError(f"浏览器预填失败：{error}") from error
            yield page
        finally:
            context.close()
            browser.close()


# ---------------------------------------------------------------------------
# 批量自动提交
# ---------------------------------------------------------------------------

def batch_submit(
    url: str,
    answer_rows: Sequence[AnswerRow],
    *,
    headless: bool = True,
    delay: float = 2.0,
    progress_callback: ProgressCallback | None = None,
) -> list[SubmitResult]:
    """对 Excel 中每一行答案依次打开问卷、填写并自动提交。"""
    if not answer_rows:
        raise BrowserPreparationError("没有可提交的答案行。")

    results: list[SubmitResult] = []
    total = len(answer_rows)
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=headless)
        except PlaywrightError as error:
            raise BrowserPreparationError(
                "未找到 Playwright Chromium。请运行：python -m playwright install chromium"
            ) from error

        try:
            for index, answer_row in enumerate(answer_rows, start=1):
                _report_progress(
                    progress_callback,
                    position=index,
                    total=total,
                    excel_row=answer_row.excel_row,
                    stage="starting",
                    message="开始处理此行。",
                )
                result = _submit_single(
                    browser,
                    url,
                    answer_row,
                    progress_callback=progress_callback,
                    position=index,
                    total=total,
                )
                results.append(result)
                _report_progress(
                    progress_callback,
                    position=index,
                    total=total,
                    excel_row=answer_row.excel_row,
                    stage="succeeded" if result.success else "failed",
                    message=result.message,
                )
                if _should_stop_batch(result):
                    _report_progress(
                        progress_callback,
                        position=index,
                        total=total,
                        excel_row=answer_row.excel_row,
                        stage="stopped",
                        message="检测到验证码、频率或安全拦截，已停止后续提交。",
                    )
                    break
                if index < total and delay > 0:
                    _report_progress(
                        progress_callback,
                        position=index,
                        total=total,
                        excel_row=answer_row.excel_row,
                        stage="waiting",
                        message=f"等待 {delay:g} 秒后处理下一行。",
                    )
                    time.sleep(delay)
        finally:
            browser.close()

    return results


def _report_progress(
    callback: ProgressCallback | None,
    *,
    position: int,
    total: int,
    excel_row: int,
    stage: str,
    message: str,
) -> None:
    if callback is not None:
        callback(
            SubmitProgress(
                position=position,
                total=total,
                excel_row=excel_row,
                stage=stage,
                message=message,
            )
        )


def _should_stop_batch(result: SubmitResult) -> bool:
    return not result.success and any(hint in result.message for hint in _STOP_BATCH_HINTS)


def _submit_single(
    browser,
    url: str,
    answer_row: AnswerRow,
    *,
    progress_callback: ProgressCallback | None = None,
    position: int = 1,
    total: int = 1,
) -> SubmitResult:
    """提交单行答案，返回结果记录。"""
    context = None
    try:
        context = browser.new_context()
        page = context.new_page()
        _report_progress(
            progress_callback,
            position=position,
            total=total,
            excel_row=answer_row.excel_row,
            stage="opening",
            message="正在打开问卷页面。",
        )
        page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        _report_progress(
            progress_callback,
            position=position,
            total=total,
            excel_row=answer_row.excel_row,
            stage="filling",
            message="正在填写题目。",
        )
        _fill_answer_row(
            page,
            answer_row,
            progress_callback=progress_callback,
            position=position,
            total=total,
        )
        _report_progress(
            progress_callback,
            position=position,
            total=total,
            excel_row=answer_row.excel_row,
            stage="submitting",
            message="正在提交并等待结果。",
        )
        _submit_form(page)
        return SubmitResult(
            excel_row=answer_row.excel_row,
            success=True,
            message="提交成功",
        )
    except (PlaywrightError, BrowserPreparationError) as error:
        return SubmitResult(
            excel_row=answer_row.excel_row,
            success=False,
            message=str(error),
        )
    finally:
        if context is not None:
            try:
                context.close()
            except PlaywrightError:
                pass


# ---------------------------------------------------------------------------
# 提交逻辑
# ---------------------------------------------------------------------------

_SUBMIT_SELECTORS = [
    "#submit_button",
    "#submit",
    "#Submit",
    ".submit",
    "#submitButton",
    "button[type='submit']",
    "a.submit",
]

_CONFIRM_BUTTON_SELECTORS = [
    ".layui-layer-btn0",
    ".modal-footer .btn-primary",
    "button.btn-primary[data-btnclass]",
    ".dialog-confirm .confirm",
    "a.ui-btn-primary[data-role='confirm']",
]

_CONFIRM_TEXTS = ("确认提交", "确定提交", "确认", "确定")
_SUCCESS_TEXTS = ("答卷成功", "问卷已提交", "提交成功", "已完成答题", "感谢您的填写")
_ERROR_SELECTORS = [
    ".errorMessage",
    ".error_tip",
    "#ErrorMessage",
    ".layui-layer-content .error",
]
_CAPTCHA_HINTS = ("验证码", "滑动", "请拖动", "安全验证", "滑块")
_STOP_BATCH_HINTS = (*_CAPTCHA_HINTS, "访问过于频繁", "操作过于频繁", "提交过于频繁", "请稍后")
_TEXT_CONTROL_SELECTOR = (
    "[contenteditable='true']:visible, textarea:visible, "
    "input[type='text']:visible, input:not([type]):visible"
)


def _submit_form(page: Page) -> None:
    """点击提交按钮，处理确认对话框，并等待结果页面。"""
    previous_body_text = _body_text(page)

    # 先注册 dialog 拦截（问卷星可能弹出原生 confirm）
    page.on("dialog", lambda dialog: dialog.accept())

    # 滚动到页面底部，确保提交按钮可见
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(0.3)

    submit_btn = None
    for selector in _SUBMIT_SELECTORS:
        candidate = page.locator(selector)
        if candidate.count():
            submit_btn = candidate.first
            break

    if submit_btn is None:
        # 尝试通过文本查找提交按钮
        for text in ("提交", "submit", "Submit", "提交问卷"):
            try:
                candidate = page.get_by_role("button", name=text, exact=False)
                if candidate.count():
                    submit_btn = candidate.first
                    break
            except PlaywrightError:
                continue
            try:
                candidate = page.locator(f"a:has-text('{text}')")
                if candidate.count():
                    submit_btn = candidate.first
                    break
            except PlaywrightError:
                continue

    if submit_btn is None:
        raise BrowserPreparationError("未找到提交按钮。")

    submit_btn.click()

    # 等待弹窗渲染后处理确认按钮
    time.sleep(0.5)
    _try_click_confirm(page)

    try:
        page.wait_for_load_state("domcontentloaded", timeout=30_000)
    except PlaywrightError:
        pass

    # 给结果页面一点渲染时间
    time.sleep(1)
    _check_result(page, previous_body_text=previous_body_text)


def _try_click_confirm(page: Page) -> None:
    """处理问卷星可能出现的页面内确认弹窗（非原生 dialog）。"""
    # 先尝试 CSS class 选择器（精确匹配弹窗按钮）
    for selector in _CONFIRM_BUTTON_SELECTORS:
        confirm = page.locator(selector)
        if confirm.count():
            try:
                confirm.first.click(timeout=3_000)
                return
            except PlaywrightError:
                continue

    # 再尝试文本匹配，但限制在可见的、可点击的按钮/链接范围内
    for text in _CONFIRM_TEXTS:
        candidates = [
            ("button", page.locator(f"button:has-text('{text}')")),
            ("a", page.locator(f"a:has-text('{text}')[role='button']")),
        ]
        for _, elements in candidates:
            if elements.count() <= 2:  # 限制匹配数量，避免误匹配
                try:
                    elements.first.click(timeout=3_000)
                    return
                except PlaywrightError:
                    continue


def _body_text(page: Page) -> str:
    try:
        return page.locator("body").inner_text(timeout=8_000)
    except PlaywrightError:
        return ""


def _check_result(
    page: Page,
    *,
    previous_body_text: str = "",
) -> None:
    """检查提交后页面是否出现成功标记或错误标记。"""
    body_text = _body_text(page)

    # 检测验证码拦截
    for hint in _CAPTCHA_HINTS:
        if hint in body_text:
            raise BrowserPreparationError(f"提交被验证码拦截（包含「{hint}」），需人工处理。")

    # 检测成功
    for success_text in _SUCCESS_TEXTS:
        if success_text in body_text and success_text not in previous_body_text:
            return

    # 通过 URL 判断：成功后通常会跳转到 wjx.cn 的结果页或带参数的感谢页
    current_url = page.url
    if "/wjx/result/" in current_url or "/result/" in current_url:
        return
    if "wjx.cn" not in current_url and "wjx.top" not in current_url:
        raise BrowserPreparationError(f"提交后页面跳转至非预期地址：{current_url}")

    # 检测错误提示元素
    for selector in _ERROR_SELECTORS:
        error_el = page.locator(selector)
        if error_el.count():
            error_text = ""
            try:
                error_text = error_el.first.text_content(timeout=3_000) or "未知错误"
            except PlaywrightError:
                error_text = "未知错误"
            raise BrowserPreparationError(f"提交失败：{error_text}")

    # 无法明确判断结果，保守地标记为失败
    raise BrowserPreparationError("提交后未能确认结果，请手动检查问卷数据。")


# ---------------------------------------------------------------------------
# 预填辅助（原有逻辑不变）
# ---------------------------------------------------------------------------

def _fill_answer_row(
    page: Page,
    answer_row: AnswerRow,
    *,
    progress_callback: ProgressCallback | None = None,
    position: int = 1,
    total: int = 1,
) -> None:
    question_total = len(answer_row.answers)
    for question_position, answer in enumerate(answer_row.answers, start=1):
        question = answer.question
        if answer.text is None and not answer.choice_values:
            continue

        _report_progress(
            progress_callback,
            position=position,
            total=total,
            excel_row=answer_row.excel_row,
            stage="filling_question",
            message=f"正在填写 Q{question.number}（第 {question_position}/{question_total} 题）。",
        )
        field = page.locator(_id_selector(question.field_id))
        field.wait_for(state="attached", timeout=15_000)
        if question.question_type == QuestionType.SINGLE_CHOICE:
            choices_by_value = {choice.value: choice for choice in question.choices}
            for value in answer.choice_values:
                choice = choices_by_value.get(value)
                if choice is None:
                    raise BrowserPreparationError(f"Q{question.number} 的答案选项已发生变化。")
                _set_choice_selected(field, choice, selected=True)
        elif question.question_type == QuestionType.MULTIPLE_CHOICE:
            selected_values = set(answer.choice_values)
            known_values = {choice.value for choice in question.choices}
            unknown_values = selected_values - known_values
            if unknown_values:
                raise BrowserPreparationError(
                    f"Q{question.number} 的答案选项已发生变化：{', '.join(sorted(unknown_values))}。"
                )
            for choice in question.choices:
                _set_choice_selected(field, choice, selected=choice.value in selected_values)
        elif question.question_type == QuestionType.SELECT:
            select = field.locator("select").first
            select.select_option(value=answer.choice_values[0])
        elif question.question_type == QuestionType.TEXT and answer.text is not None:
            _fill_text_answer(field, question, answer.text)


def _fill_text_answer(field: Locator, question: Question, text: str) -> None:
    text_control = field.locator(_TEXT_CONTROL_SELECTOR).first
    try:
        text_control.wait_for(state="visible", timeout=15_000)
        text_control.scroll_into_view_if_needed()
        text_control.fill(text)
        is_contenteditable = text_control.get_attribute("contenteditable") == "true"
        if is_contenteditable:
            text_control.evaluate("element => element.blur()")
            actual_text = text_control.inner_text()
        else:
            actual_text = text_control.input_value()
    except PlaywrightError as error:
        raise BrowserPreparationError(f"Q{question.number} 未找到可填写的文本控件。") from error

    if actual_text != text:
        raise BrowserPreparationError(f"Q{question.number} 的填空内容未能写入页面。")

    if is_contenteditable:
        _verify_hidden_text_value(field, question, text)


def _verify_hidden_text_value(field: Locator, question: Question, text: str) -> None:
    hidden_input = field.locator("input.ui-input-text").first
    if not hidden_input.count():
        return
    try:
        if hidden_input.input_value() != text:
            raise BrowserPreparationError(f"Q{question.number} 的填空内容未同步到问卷。")
    except PlaywrightError as error:
        raise BrowserPreparationError(f"Q{question.number} 的填空状态无法验证。") from error


def _set_choice_selected(field: Locator, choice: Choice, *, selected: bool) -> None:
    """将一个单选或多选控件收敛到指定状态，并确认原生 input 已同步。"""
    input_selector = _id_selector(choice.input_id) if choice.input_id else _value_selector(choice.value)
    input_control = field.locator(input_selector).first
    try:
        input_control.wait_for(state="attached", timeout=15_000)
        if input_control.is_checked() == selected:
            return
    except PlaywrightError as error:
        raise BrowserPreparationError(f"选项“{choice.label or choice.value}”不可用。") from error

    visual_control = field.locator(
        f"{input_selector} + .jqradio, {input_selector} + .jqcheck, {input_selector} + .jqcheckbox"
    )
    if visual_control.count():
        try:
            visual_control.first.click(timeout=3_000)
        except PlaywrightError:
            pass

    try:
        if input_control.is_checked() != selected:
            input_control.evaluate("input => input.click()")
        if input_control.is_checked() != selected:
            state = "选中" if selected else "取消选中"
            raise BrowserPreparationError(f"无法{state}选项“{choice.label or choice.value}”。")
    except PlaywrightError as error:
        state = "选中" if selected else "取消选中"
        raise BrowserPreparationError(f"无法{state}选项“{choice.label or choice.value}”。") from error


def _id_selector(element_id: str | None) -> str:
    if not element_id:
        raise BrowserPreparationError("问卷控件缺少 id，无法安全预填。")
    return f"[id={json.dumps(element_id)}]"


def _value_selector(value: str) -> str:
    return f"input[value={json.dumps(value)}]"
