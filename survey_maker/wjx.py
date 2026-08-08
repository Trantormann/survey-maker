"""问卷星页面中公开题目结构的只读解析。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from html.parser import HTMLParser
from typing import Iterable
from urllib.parse import urljoin, urlparse

import requests


_WJX_DOMAINS = ("wjx.cn", "wjx.top")
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_VOID_TAGS = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "track", "wbr"}


class QuestionType(str, Enum):
    SINGLE_CHOICE = "single_choice"
    MULTIPLE_CHOICE = "multiple_choice"
    SELECT = "select"
    TEXT = "text"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Choice:
    value: str
    label: str
    input_id: str | None = None


@dataclass(frozen=True)
class Question:
    number: int
    title: str
    question_type: QuestionType
    required: bool
    field_id: str
    field_name: str | None
    choices: tuple[Choice, ...] = ()
    max_choices: int | None = None
    min_choices: int | None = None


@dataclass
class _QuestionDraft:
    number: int
    required: bool
    field_id: str
    root_depth: int
    title_parts: list[str]
    choices: list[Choice]
    input_types: set[str]
    field_name: str | None = None
    has_select: bool = False
    has_textarea: bool = False
    last_choice_index: int | None = None
    max_choices: int | None = None
    min_choices: int | None = None


class _QuestionParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.questions: list[Question] = []
        self._depth = 0
        self._active: _QuestionDraft | None = None
        self._title_depth: int | None = None
        self._label_depth: int | None = None
        self._label_for: str | None = None
        self._label_parts: list[str] = []
        self._option_depth: int | None = None
        self._option_value: str | None = None
        self._option_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        is_void = tag in _VOID_TAGS
        if not is_void:
            self._depth += 1

        if self._active is None:
            self._start_question(tag, attributes)
            return

        self._capture_start(tag, attributes)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _VOID_TAGS:
            self.handle_starttag(tag, attrs)
            return
        self.handle_starttag(tag, attrs)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if self._active is not None:
            self._capture_end(tag)
            if tag == "div" and self._depth == self._active.root_depth:
                self._finish_question()

        if tag not in _VOID_TAGS:
            self._depth -= 1

    def handle_data(self, data: str) -> None:
        if self._active is None:
            return
        text = " ".join(data.split())
        if not text:
            return
        if self._title_depth is not None:
            self._active.title_parts.append(text)
        if self._label_depth is not None:
            self._label_parts.append(text)
        if self._option_depth is not None:
            self._option_parts.append(text)

    def _start_question(self, tag: str, attributes: dict[str, str]) -> None:
        classes = set(attributes.get("class", "").split())
        topic = attributes.get("topic")
        if tag != "div" or not topic or "field" not in classes:
            return

        try:
            number = int(topic)
        except ValueError:
            return

        self._active = _QuestionDraft(
            number=number,
            required=attributes.get("req", "").lower() in {"1", "true", "yes"},
            field_id=attributes.get("id", f"div{number}"),
            root_depth=self._depth,
            title_parts=[],
            choices=[],
            input_types=set(),
            max_choices=_positive_int(attributes.get("maxvalue")),
            min_choices=_positive_int(attributes.get("minvalue")),
        )

    def _capture_start(self, tag: str, attributes: dict[str, str]) -> None:
        assert self._active is not None
        classes = set(attributes.get("class", "").split())

        if tag == "div" and "topichtml" in classes:
            self._title_depth = self._depth
        elif tag == "label" or (tag == "div" and "label" in classes):
            self._label_depth = self._depth
            self._label_for = attributes.get("for") or None
            self._label_parts = []
        elif tag == "input":
            input_type = attributes.get("type", "text").lower()
            if input_type not in {"hidden", "submit", "button", "reset"}:
                self._active.input_types.add(input_type)
            if not self._active.field_name and attributes.get("name"):
                self._active.field_name = attributes["name"]
            if input_type in {"radio", "checkbox"}:
                self._active.choices.append(
                    Choice(
                        value=attributes.get("value", ""),
                        label="",
                        input_id=attributes.get("id") or None,
                    )
                )
                self._active.last_choice_index = len(self._active.choices) - 1
        elif tag == "textarea":
            self._active.has_textarea = True
            if not self._active.field_name and attributes.get("name"):
                self._active.field_name = attributes["name"]
        elif tag == "select":
            self._active.has_select = True
            if not self._active.field_name and attributes.get("name"):
                self._active.field_name = attributes["name"]
        elif tag == "option":
            self._option_depth = self._depth
            self._option_value = attributes.get("value", "")
            self._option_parts = []

    def _capture_end(self, tag: str) -> None:
        assert self._active is not None
        if self._title_depth is not None and tag == "div" and self._depth == self._title_depth:
            self._title_depth = None
        if self._label_depth is not None and tag in {"label", "div"} and self._depth == self._label_depth:
            self._apply_label()
            self._label_depth = None
            self._label_for = None
            self._label_parts = []
        if self._option_depth is not None and tag == "option" and self._depth == self._option_depth:
            self._active.choices.append(
                Choice(value=self._option_value or "", label=" ".join(self._option_parts))
            )
            self._option_depth = None
            self._option_value = None
            self._option_parts = []

    def _apply_label(self) -> None:
        assert self._active is not None
        label = " ".join(self._label_parts)
        if not label:
            return
        choice_index = self._active.last_choice_index
        if self._label_for:
            for index, choice in enumerate(self._active.choices):
                if choice.input_id == self._label_for:
                    choice_index = index
                    break
        if choice_index is None:
            return
        choice = self._active.choices[choice_index]
        self._active.choices[choice_index] = Choice(choice.value, label, choice.input_id)

    def _finish_question(self) -> None:
        assert self._active is not None
        title = " ".join(self._active.title_parts).strip()
        question_type = _question_type(self._active)
        self.questions.append(
            Question(
                number=self._active.number,
                title=title or f"第 {self._active.number} 题",
                question_type=question_type,
                required=self._active.required,
                field_id=self._active.field_id,
                field_name=self._active.field_name,
                choices=tuple(self._active.choices),
                max_choices=self._active.max_choices,
                min_choices=self._active.min_choices,
            )
        )
        self._active = None
        self._title_depth = None
        self._label_depth = None
        self._option_depth = None


def _positive_int(value: str | None) -> int | None:
    try:
        parsed = int(value or "")
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _question_type(draft: _QuestionDraft) -> QuestionType:
    if "radio" in draft.input_types:
        return QuestionType.SINGLE_CHOICE
    if "checkbox" in draft.input_types:
        return QuestionType.MULTIPLE_CHOICE
    if draft.has_select:
        return QuestionType.SELECT
    if draft.has_textarea or draft.input_types:
        return QuestionType.TEXT
    return QuestionType.UNSUPPORTED


def parse_questions(html: str) -> list[Question]:
    """从问卷星页面 HTML 中提取题目，完全在本地处理。"""
    parser = _QuestionParser()
    parser.feed(html)
    parser.close()
    return parser.questions


def _validate_wjx_url(url: str) -> None:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower().rstrip(".")
    is_wjx_host = any(host == domain or host.endswith(f".{domain}") for domain in _WJX_DOMAINS)
    if parsed.scheme not in {"http", "https"} or not is_wjx_host:
        raise ValueError("仅支持以 wjx.cn 或 wjx.top 结尾的 HTTP(S) 问卷星链接。")


def _follow_safe_redirects(url: str, *, timeout: float) -> requests.Response:
    current_url = url
    headers = {"User-Agent": "SurveyMaker/0.1 (authorized questionnaire testing)"}
    for _ in range(5):
        _validate_wjx_url(current_url)
        response = requests.get(current_url, headers=headers, timeout=timeout, allow_redirects=False)
        if response.status_code not in _REDIRECT_CODES:
            return response
        location = response.headers.get("Location")
        if not location:
            return response
        current_url = urljoin(current_url, location)
    raise ValueError("问卷链接重定向次数过多。")


def fetch_questions(url: str, *, timeout: float = 30) -> list[Question]:
    """读取已获授权的问卷链接，并返回可见题目结构。"""
    _validate_wjx_url(url)
    response = _follow_safe_redirects(url, timeout=timeout)
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "").lower()
    if content_type and "html" not in content_type:
        raise ValueError("链接返回的内容不是 HTML 页面。")
    return parse_questions(response.text)


def iter_choice_labels(question: Question) -> Iterable[str]:
    """返回适合展示在 Excel 说明中的选项标签。"""
    return (choice.label or choice.value for choice in question.choices)