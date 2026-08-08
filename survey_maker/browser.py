"""在浏览器中预填与批量提交问卷星答案。"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import json
import random
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
        browser = _launch_human_browser(playwright, headless=False)

        context = _new_human_context(browser)
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
    jitter: float = 0.0,
    speed: str = "fast",
    progress_callback: ProgressCallback | None = None,
) -> list[SubmitResult]:
    """对 Excel 中每一行答案依次打开问卷、填写并自动提交。

    delay 为基础间隔秒数；jitter 为附加随机抖动上限（秒），
    实际等待 = delay + random.uniform(0, jitter)，使提交节奏更接近真人。
    speed 为 'fast' 时跳过高拟人行为模拟，'human' 时启用贝塞尔鼠标等。
    """
    if not answer_rows:
        raise BrowserPreparationError("没有可提交的答案行。")

    results: list[SubmitResult] = []
    total = len(answer_rows)
    with sync_playwright() as playwright:
        browser = _launch_human_browser(playwright, headless=headless)

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
                    speed=speed,
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
                    wait = delay + (random.uniform(0, jitter) if jitter > 0 else 0)
                    _report_progress(
                        progress_callback,
                        position=index,
                        total=total,
                        excel_row=answer_row.excel_row,
                        stage="waiting",
                        message=f"等待 {wait:.1f} 秒后处理下一行。",
                    )
                    time.sleep(wait)
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
    speed: str = "fast",
    progress_callback: ProgressCallback | None = None,
    position: int = 1,
    total: int = 1,
) -> SubmitResult:
    """提交单行答案，返回结果记录。"""
    context = None
    try:
        context = _new_human_context(browser)
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
            speed=speed,
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
        _submit_form(page, speed=speed)
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
_RESULT_URL_PATTERNS = ("/wjx/result/", "/result/", "completemobile2.aspx", "resultquery.aspx")
_ERROR_SELECTORS = [
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
_WJX_SUBMIT_SELECTOR = "#ctlNext"
_WJX_SUBMIT_WAIT_MS = 10_000

# 拟人浏览器启动参数：隐藏自动化特征
_HUMAN_LAUNCH_ARGS = [
    "--disable-blink-automation",
    "--disable-features=IsolateOrigins,site-per-process",
]
_HUMAN_VIEWPORT = {"width": 1366, "height": 768}
_HUMAN_LOCALE = "zh-CN"

# 综合伪装脚本：覆盖 WebDriver、Canvas、WebGL、权限 API 等检测维度
_STEALTH_INIT_SCRIPT = r"""
(() => {
    // 1. 移除 navigator.webdriver 标记
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});

    // 2. 伪造插件列表（正常浏览器至少 3 个）
    Object.defineProperty(navigator, 'plugins', {
        get: () => {
            const arr = [
                {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
                {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeoexfofkobgkfhjgc', description: ''},
                {name: 'Native Client', filename: 'internal-nacl-plugin', description: ''}
            ];
            arr.item = i => arr[i] || null;
            arr.namedItem = n => arr.find(p => p.name === n) || null;
            arr.refresh = () => {};
            return arr;
        }
    });

    // 3. 伪造语言列表
    Object.defineProperty(navigator, 'languages', {get: () => ['zh-CN', 'zh', 'en']});

    // 4. 伪造硬件信息
    Object.defineProperty(navigator, 'hardwareConcurrency', {get: () => 8});
    Object.defineProperty(navigator, 'deviceMemory', {get: () => 8});
    Object.defineProperty(navigator, 'platform', {get: () => 'Win32'});

    // 5. 伪造 permissions API（headless 中 Notification.permission 可能异常）
    if (!window.Notification) window.Notification = {permission: 'default', requestPermission: () => Promise.resolve('default')};
    const origQuery = navigator.permissions && navigator.permissions.query;
    if (navigator.permissions) {
        navigator.permissions.query = (params) =>
            params && params.name === 'notifications'
                ? Promise.resolve({state: Notification.permission, onchange: null})
                : origQuery ? origQuery.call(navigator.permissions, params) : Promise.resolve({state: 'prompt'});
    }

    // 6. Canvas 指纹随机化：在 toDataURL / getImageData 中注入微小噪声
    const origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(...args) {
        try {
            const ctx = this.getContext('2d');
            if (ctx && this.width > 0 && this.height > 0) {
                const imageData = ctx.getImageData(0, 0, this.width, this.height);
                for (let i = 0; i < imageData.data.length; i += 4) {
                    // 注入人眼不可见但可改变哈希的微小噪声
                    imageData.data[i] ^= 1;
                }
                ctx.putImageData(imageData, 0, 0);
            }
        } catch(e) {}
        return origToDataURL.apply(this, args);
    };

    // 7. WebGL 伪装：返回常见 GPU 信息
    const getParameterOrig = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(param) {
        // UNMASKED_VENDOR_WEBGL = 0x9245, UNMASKED_RENDERER_WEBGL = 0x9246
        if (param === 0x9245) return 'Google Inc. (Intel)';
        if (param === 0x9246) return 'ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.1)';
        return getParameterOrig.call(this, param);
    };
    if (typeof WebGL2RenderingContext !== 'undefined') {
        const getParameter2Orig = WebGL2RenderingContext.prototype.getParameter;
        WebGL2RenderingContext.prototype.getParameter = function(param) {
            if (param === 0x9245) return 'Google Inc. (Intel)';
            if (param === 0x9246) return 'ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.1)';
            return getParameter2Orig.call(this, param);
        };
    }

    // 8. 伪造 chrome 对象（正常 Chrome 浏览器存在 window.chrome）
    if (!window.chrome) {
        window.chrome = {runtime: {}, app: {isInstalled: false}};
    }
})();
"""


def _launch_human_browser(playwright, *, headless: bool):
    """以拟人特征启动 Chromium，降低被平台识别为自动化的概率。"""
    try:
        browser = playwright.chromium.launch(
            headless=headless,
            args=_HUMAN_LAUNCH_ARGS,
        )
    except PlaywrightError as error:
        raise BrowserPreparationError(
            "未找到 Playwright Chromium。请运行：python -m playwright install chromium"
        ) from error
    return browser


# ---------------------------------------------------------------------------
# 拟人行为模拟：鼠标轨迹、点击节奏、打字节奏
# ---------------------------------------------------------------------------

def _fast_click(page: Page, locator: Locator, *, timeout: int = 5_000) -> None:
    """快速点击：无贝塞尔轨迹、无停顿。"""
    locator.click(timeout=timeout)


def _fast_type(page: Page, locator: Locator, text: str) -> None:
    """快速填写：直接用 fill() 而非逐字符输入。"""
    locator.fill(text)


def _human_mouse_move(page: Page, target_x: float, target_y: float) -> None:
    """模拟真人鼠标移动：沿贝塞尔曲线从随机起点移动到目标位置。"""
    try:
        start_x = random.uniform(100, 800)
        start_y = random.uniform(100, 500)
        steps = random.randint(15, 30)
        ctrl_x = (start_x + target_x) / 2 + random.uniform(-100, 100)
        ctrl_y = (start_y + target_y) / 2 + random.uniform(-80, 80)
        for i in range(1, steps + 1):
            t = i / steps
            x = (1 - t) ** 2 * start_x + 2 * (1 - t) * t * ctrl_x + t ** 2 * target_x
            y = (1 - t) ** 2 * start_y + 2 * (1 - t) * t * ctrl_y + t ** 2 * target_y
            page.mouse.move(x, y)
            time.sleep(random.uniform(0.008, 0.025))
    except PlaywrightError:
        pass


def _human_click(page: Page, locator: Locator, *, timeout: int = 5_000) -> None:
    """模拟真人点击：先将鼠标移动到元素附近，再点击。"""
    try:
        box = locator.bounding_box()
        if box:
            target_x = box["x"] + box["width"] / 2 + random.uniform(-5, 5)
            target_y = box["y"] + box["height"] / 2 + random.uniform(-3, 3)
            _human_mouse_move(page, target_x, target_y)
            time.sleep(random.uniform(0.05, 0.2))
        locator.click(timeout=timeout)
    except PlaywrightError:
        locator.click(timeout=timeout)


def _human_type(page: Page, locator: Locator, text: str) -> None:
    """模拟真人打字：逐字符输入，每个字符间随机停顿。"""
    try:
        locator.click()
        locator.fill("")  # 先清空
        for char in text:
            page.keyboard.type(char)
            time.sleep(random.uniform(0.05, 0.18))
    except PlaywrightError:
        locator.fill(text)


def _new_human_context(browser):
    """创建带拟人特征的浏览器上下文。"""
    context = browser.new_context(
        viewport=_HUMAN_VIEWPORT,
        locale=_HUMAN_LOCALE,
    )
    context.add_init_script(_STEALTH_INIT_SCRIPT)
    return context


def _submit_form(page: Page, *, speed: str = "fast") -> None:
    """点击提交按钮，处理确认对话框，并等待结果页面。"""
    previous_body_text = _body_text(page)
    click_fn: Callable[..., None] = _human_click if speed == "human" else _fast_click

    # 先注册 dialog 拦截（问卷星可能弹出原生 confirm）
    page.on("dialog", lambda dialog: dialog.accept())

    # 滚动到页面底部，确保提交按钮可见
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    time.sleep(random.uniform(0.5, 1.5) if speed == "human" else 0.3)

    submit_btn = _find_submit_button(page)

    if submit_btn is None:
        raise BrowserPreparationError("未找到可见的提交按钮。请确认问卷已加载到最后一页。")

    click_fn(page, submit_btn)

    # 等待可能的确认弹窗（非 WJX 平台）
    time.sleep(0.5)
    _try_click_confirm(page, click_fn=click_fn)

    # 问卷星通过 AJAX 提交，成功后会将文本写入 #ValError，然后 1.5 秒后跳转。
    _wait_for_submit_result(page)

    _check_result(page, previous_body_text=previous_body_text)


def _find_submit_button(page: Page) -> Locator | None:
    """定位问卷星或通用问卷页面中当前可见的最终提交控件。"""
    wjx_submit = _first_visible(page.locator(_WJX_SUBMIT_SELECTOR))
    if wjx_submit is not None and _is_wjx_submit_control(wjx_submit):
        return wjx_submit

    for selector in _SUBMIT_SELECTORS:
        candidate = _first_visible(page.locator(selector))
        if candidate is not None:
            return candidate

    for text in ("提交", "submit", "Submit", "提交问卷"):
        try:
            candidate = _first_visible(page.get_by_role("button", name=text, exact=False))
            if candidate is not None:
                return candidate
        except PlaywrightError:
            continue
        try:
            candidate = _first_visible(page.locator(f"a:has-text('{text}')"))
            if candidate is not None:
                return candidate
        except PlaywrightError:
            continue

    # 问卷星可能在初始 DOM 加载后才注入 ctlNext，因此只在没有其他候选时等待它。
    delayed_wjx_submit = page.locator(_WJX_SUBMIT_SELECTOR).first
    try:
        delayed_wjx_submit.wait_for(state="visible", timeout=_WJX_SUBMIT_WAIT_MS)
        if _is_wjx_submit_control(delayed_wjx_submit):
            return delayed_wjx_submit
    except PlaywrightError:
        pass
    return None


def _is_wjx_submit_control(control: Locator) -> bool:
    try:
        label = " ".join((control.inner_text() or "").split())
    except PlaywrightError:
        return False
    return "提交" in label or label.casefold() == "submit"


def _first_visible(candidates: Locator) -> Locator | None:
    try:
        for index in range(candidates.count()):
            candidate = candidates.nth(index)
            if candidate.is_visible():
                return candidate
    except PlaywrightError:
        return None
    return None


def _wait_for_submit_result(page: Page) -> None:
    """等待 AJAX 提交完成：#ValError 出现文本或页面开始跳转。"""
    try:
        page.wait_for_function(
            """() => {
                const valError = document.getElementById('ValError');
                if (valError && valError.textContent.trim()) return true;
                const captchaTit = document.getElementById('captchaTit');
                if (captchaTit && captchaTit.textContent.trim()) return true;
                return false;
            }""",
            timeout=15_000,
        )
    except PlaywrightError:
        pass

    # 给页面跳转额外时间（问卷星成功后 1.5 秒才跳转）
    try:
        page.wait_for_url(
            lambda url: any(pattern in url for pattern in _RESULT_URL_PATTERNS),
            timeout=5_000,
        )
    except PlaywrightError:
        pass


def _try_click_confirm(page: Page, *, click_fn: Callable[..., None] = _fast_click) -> None:
    """处理问卷星可能出现的页面内确认弹窗（非原生 dialog）。"""
    # 先尝试 CSS class 选择器（精确匹配弹窗按钮）
    for selector in _CONFIRM_BUTTON_SELECTORS:
        confirm = page.locator(selector)
        if confirm.count():
            try:
                click_fn(page, confirm.first, timeout=3_000)
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
            if 0 < elements.count() <= 2:  # 限制匹配数量，避免误匹配
                try:
                    click_fn(page, elements.first, timeout=3_000)
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
    if any(pattern in current_url for pattern in _RESULT_URL_PATTERNS):
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
    speed: str = "fast",
    progress_callback: ProgressCallback | None = None,
    position: int = 1,
    total: int = 1,
) -> None:
    question_total = len(answer_row.answers)
    click_fn: Callable[..., None] = _human_click if speed == "human" else _fast_click
    type_fn: Callable[..., None] = _human_type if speed == "human" else _fast_type

    for question_position, answer in enumerate(answer_row.answers, start=1):
        question = answer.question
        if answer.text is None and not answer.choice_values:
            continue

        # 题目间随机停顿，模拟真人阅读和思考时间
        if speed == "human" and question_position > 1:
            time.sleep(random.uniform(0.8, 3.0))

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
                _set_choice_selected(page, field, choice, selected=True, click_fn=click_fn)
        elif question.question_type == QuestionType.MULTIPLE_CHOICE:
            selected_values = set(answer.choice_values)
            known_values = {choice.value for choice in question.choices}
            unknown_values = selected_values - known_values
            if unknown_values:
                raise BrowserPreparationError(
                    f"Q{question.number} 的答案选项已发生变化：{', '.join(sorted(unknown_values))}。"
                )
            for choice in question.choices:
                _set_choice_selected(page, field, choice, selected=choice.value in selected_values, click_fn=click_fn)
        elif question.question_type == QuestionType.SELECT:
            select = field.locator("select").first
            select.select_option(value=answer.choice_values[0])
        elif question.question_type == QuestionType.TEXT and answer.text is not None:
            _fill_text_answer(page, field, question, answer.text, type_fn=type_fn)


def _fill_text_answer(page: Page, field: Locator, question: Question, text: str, *, type_fn: Callable[..., None] = _fast_type) -> None:
    text_control = field.locator(_TEXT_CONTROL_SELECTOR).first
    try:
        text_control.wait_for(state="visible", timeout=15_000)
        text_control.scroll_into_view_if_needed()
        type_fn(page, text_control, text)
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


def _set_choice_selected(page: Page, field: Locator, choice: Choice, *, selected: bool, click_fn: Callable[..., None] = _fast_click) -> None:
    """将一个单选或多选控件收敛到指定状态，并确认原生 input 已同步。"""
    input_selector = _id_selector(choice.input_id) if choice.input_id else _value_selector(choice.value)
    input_control = field.locator(input_selector).first
    try:
        input_control.wait_for(state="attached", timeout=15_000)
        if input_control.is_checked() == selected:
            return
    except PlaywrightError as error:
        raise BrowserPreparationError(f'选项"{choice.label or choice.value}"不可用。') from error

    visual_control = field.locator(
        f"{input_selector} + .jqradio, {input_selector} + .jqcheck, {input_selector} + .jqcheckbox"
    )
    if visual_control.count():
        try:
            click_fn(page, visual_control.first, timeout=3_000)
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
