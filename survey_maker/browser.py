"""在可见浏览器中预填一行已校验的问卷答案。"""

from __future__ import annotations

from contextlib import contextmanager
import json
from typing import Iterator

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

from .excel import AnswerRow
from .wjx import Choice, Question, QuestionType


class BrowserPreparationError(RuntimeError):
    """无法加载或预填问卷页面时抛出。"""


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


def _fill_answer_row(page: Page, answer_row: AnswerRow) -> None:
    for answer in answer_row.answers:
        question = answer.question
        if answer.text is None and not answer.choice_values:
            continue

        field = page.locator(_id_selector(question.field_id))
        field.wait_for(state="attached", timeout=15_000)
        if question.question_type in {QuestionType.SINGLE_CHOICE, QuestionType.MULTIPLE_CHOICE}:
            choices_by_value = {choice.value: choice for choice in question.choices}
            for value in answer.choice_values:
                choice = choices_by_value.get(value)
                if choice is None:
                    raise BrowserPreparationError(f"Q{question.number} 的答案选项已发生变化。")
                _click_choice(field, choice)
        elif question.question_type == QuestionType.SELECT:
            select = field.locator("select").first
            select.select_option(value=answer.choice_values[0])
        elif question.question_type == QuestionType.TEXT and answer.text is not None:
            text_box = field.locator("textarea, input[type='text'], input:not([type])").first
            text_box.fill(answer.text)


def _click_choice(field: object, choice: Choice) -> None:
    input_selector = _id_selector(choice.input_id) if choice.input_id else _value_selector(choice.value)
    visual_control = field.locator(
        f"{input_selector} + .jqradio, {input_selector} + .jqcheck, {input_selector} + .jqcheckbox"
    )
    if visual_control.count():
        visual_control.first.click()
        return
    field.locator(input_selector).check(force=True)


def _id_selector(element_id: str | None) -> str:
    if not element_id:
        raise BrowserPreparationError("问卷控件缺少 id，无法安全预填。")
    return f"[id={json.dumps(element_id)}]"


def _value_selector(value: str) -> str:
    return f"input[value={json.dumps(value)}]"