import unittest
from unittest.mock import patch

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from survey_maker.browser import (
    BrowserPreparationError,
    SubmitResult,
    _check_result,
    _fill_answer_row,
    _submit_form,
    _submit_single,
    batch_submit,
)
from survey_maker.excel import AnswerRow, AnswerValue
from survey_maker.wjx import Choice, Question, QuestionType


class BrowserFillTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.playwright = sync_playwright().start()
        try:
            cls.browser = cls.playwright.chromium.launch(headless=True)
        except PlaywrightError as error:
            cls.playwright.stop()
            raise unittest.SkipTest(f"Chromium 不可用：{error}") from error

    @classmethod
    def tearDownClass(cls) -> None:
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self) -> None:
        self.page = self.browser.new_page()
        self.page.set_content(
            """
            <div class="field" id="div1">
              <span><input id="q1_1" type="radio" name="q1" value="1" style="display:none"><a class="jqradio" style="display:block;width:20px;height:20px" onclick="document.getElementById('q1_1').checked = true">A</a></span>
              <span><input id="q1_2" type="radio" name="q1" value="2" style="display:none"><a class="jqradio" style="display:block;width:20px;height:20px" onclick="document.getElementById('q1_2').checked = true">B</a></span>
            </div>
            <div class="field" id="div2">
              <span><input id="q2_1" type="checkbox" name="q2" value="1" style="display:none"><a class="jqcheck" style="display:block;width:20px;height:20px" onclick="document.getElementById('q2_1').checked = !document.getElementById('q2_1').checked">A</a></span>
              <span><input id="q2_2" type="checkbox" name="q2" value="2" style="display:none"><a class="jqcheck" style="display:block;width:20px;height:20px" onclick="document.getElementById('q2_2').checked = !document.getElementById('q2_2').checked">B</a></span>
            </div>
            <div class="field" id="div3"><textarea name="q3"></textarea></div>
            """
        )

    def tearDown(self) -> None:
        self.page.close()

    def test_fills_hidden_choice_controls_and_text(self) -> None:
        single = Question(
            number=1,
            title="单选",
            question_type=QuestionType.SINGLE_CHOICE,
            required=True,
            field_id="div1",
            field_name="q1",
            choices=(Choice("1", "A", "q1_1"), Choice("2", "B", "q1_2")),
        )
        multiple = Question(
            number=2,
            title="多选",
            question_type=QuestionType.MULTIPLE_CHOICE,
            required=True,
            field_id="div2",
            field_name="q2",
            choices=(Choice("1", "A", "q2_1"), Choice("2", "B", "q2_2")),
        )
        text = Question(
            number=3,
            title="文本",
            question_type=QuestionType.TEXT,
            required=False,
            field_id="div3",
            field_name="q3",
        )
        answer_row = AnswerRow(
            excel_row=2,
            answers=(
                AnswerValue(question=single, choice_values=("2",)),
                AnswerValue(question=multiple, choice_values=("1", "2")),
                AnswerValue(question=text, text="需要补齐服务"),
            ),
        )
        events = []

        _fill_answer_row(self.page, answer_row, progress_callback=events.append, position=2, total=4)

        self.assertTrue(self.page.locator("#q1_2").is_checked())
        self.assertTrue(self.page.locator("#q2_1").is_checked())
        self.assertTrue(self.page.locator("#q2_2").is_checked())
        self.assertEqual(self.page.locator("textarea[name='q3']").input_value(), "需要补齐服务")
        self.assertEqual(
            [(event.stage, event.message) for event in events],
            [
                ("filling_question", "正在填写 Q1（第 1/3 题）。"),
                ("filling_question", "正在填写 Q2（第 2/3 题）。"),
                ("filling_question", "正在填写 Q3（第 3/3 题）。"),
            ],
        )

    def test_multiple_choice_matches_the_requested_set(self) -> None:
        self.page.set_content(
            """
            <div class="field" id="div2">
              <span><input id="q2_1" type="checkbox" name="q2" value="1" checked style="display:none"><a class="jqcheck" style="display:block;width:20px;height:20px" onclick="document.getElementById('q2_1').checked = !document.getElementById('q2_1').checked">A</a></span>
              <span><input id="q2_2" type="checkbox" name="q2" value="2" checked style="display:none"><a class="jqcheck" style="display:block;width:20px;height:20px" onclick="document.getElementById('q2_2').checked = !document.getElementById('q2_2').checked">B</a></span>
            </div>
            """
        )
        multiple = Question(
            number=2,
            title="多选",
            question_type=QuestionType.MULTIPLE_CHOICE,
            required=True,
            field_id="div2",
            field_name="q2",
            choices=(Choice("1", "A", "q2_1"), Choice("2", "B", "q2_2")),
        )
        answer_row = AnswerRow(
            excel_row=2,
            answers=(AnswerValue(question=multiple, choice_values=("1",)),),
        )

        _fill_answer_row(self.page, answer_row)

        self.assertTrue(self.page.locator("#q2_1").is_checked())
        self.assertFalse(self.page.locator("#q2_2").is_checked())

    def test_multiple_choice_falls_back_to_native_checkbox_state(self) -> None:
        self.page.set_content(
            """
            <div class="field" id="div4">
              <span><input id="q4_1" type="checkbox" name="q4" value="1" style="display:none"><a class="jqcheck" style="display:block;width:20px;height:20px">A</a></span>
            </div>
            """
        )
        multiple = Question(
            number=4,
            title="多选",
            question_type=QuestionType.MULTIPLE_CHOICE,
            required=True,
            field_id="div4",
            field_name="q4",
            choices=(Choice("1", "A", "q4_1"),),
        )
        answer_row = AnswerRow(
            excel_row=2,
            answers=(AnswerValue(question=multiple, choice_values=("1",)),),
        )

        _fill_answer_row(self.page, answer_row)

        self.assertTrue(self.page.locator("#q4_1").is_checked())

    def test_fills_wjx_contenteditable_text_and_syncs_hidden_input(self) -> None:
        self.page.set_content(
            """
            <div class="field" id="div16">
              <input class="ui-input-text" type="text" style="display:none">
                            <label class="textEdit">
                                <span class="textCont" contenteditable="true" style="display:inline-block;width:200px;min-height:20px" oninput="document.querySelector('#div16 input').value = this.textContent"></span>
                            </label>
            </div>
            """
        )
        text = Question(
            number=16,
            title="填空",
            question_type=QuestionType.TEXT,
            required=True,
            field_id="div16",
            field_name=None,
        )
        answer_row = AnswerRow(
            excel_row=2,
            answers=(AnswerValue(question=text, text="定时上门维修站"),),
        )

        _fill_answer_row(self.page, answer_row)

        self.assertEqual(self.page.locator("#div16 .textCont").inner_text(), "定时上门维修站")
        self.assertEqual(self.page.locator("#div16 input.ui-input-text").input_value(), "定时上门维修站")

    def test_submit_handles_inline_confirmation_and_new_success_text(self) -> None:
        self.page.set_content(
            """
            <button id="submit_button" type="button" onclick="document.getElementById('confirm').hidden = false">提交</button>
            <div id="confirm" hidden>
              <button class="layui-layer-btn0" type="button" onclick="document.getElementById('result').textContent = '提交成功'">确认提交</button>
            </div>
            <p id="result"></p>
            """
        )

        with patch("survey_maker.browser.time.sleep"):
            _submit_form(self.page)

        self.assertEqual(self.page.locator("#result").inner_text(), "提交成功")

    def test_submit_handles_wjx_ctl_next_div(self) -> None:
        self.page.set_content(
            """
            <div id="ctlNext" class="submitbtn" onclick="document.getElementById('result').textContent = '提交成功'">提交</div>
            <p id="result"></p>
            """
        )

        with patch("survey_maker.browser.time.sleep"):
            _submit_form(self.page)

        self.assertEqual(self.page.locator("#result").inner_text(), "提交成功")

    def test_result_detection_rejects_captcha_page(self) -> None:
        self.page.set_content("<p>请完成验证码后继续</p>")

        with self.assertRaisesRegex(BrowserPreparationError, "验证码"):
            _check_result(self.page)

    def test_context_creation_failure_is_returned_as_a_row_failure(self) -> None:
        class BrokenBrowser:
            def new_context(self):
                raise BrowserPreparationError("浏览器上下文不可用")

        result = _submit_single(BrokenBrowser(), "https://v.wjx.cn/vm/example.aspx", AnswerRow(2, ()))

        self.assertEqual(result.excel_row, 2)
        self.assertFalse(result.success)
        self.assertIn("浏览器上下文不可用", result.message)

    def test_submit_reports_opening_filling_and_submitting_stages(self) -> None:
        class FakePage:
            def goto(self, *_args, **_kwargs) -> None:
                pass

        class FakeContext:
            def new_page(self) -> FakePage:
                return FakePage()

            def close(self) -> None:
                pass

        class FakeBrowser:
            def new_context(self) -> FakeContext:
                return FakeContext()

        events = []
        with (
            patch("survey_maker.browser._fill_answer_row"),
            patch("survey_maker.browser._submit_form"),
        ):
            result = _submit_single(
                FakeBrowser(),
                "https://v.wjx.cn/vm/example.aspx",
                AnswerRow(2, ()),
                progress_callback=events.append,
                position=1,
                total=2,
            )

        self.assertTrue(result.success)
        self.assertEqual(
            [(event.stage, event.position, event.total, event.excel_row) for event in events],
            [("opening", 1, 2, 2), ("filling", 1, 2, 2), ("submitting", 1, 2, 2)],
        )

    def test_batch_stops_after_captcha_failure(self) -> None:
        class FakeBrowser:
            def close(self) -> None:
                pass

        class FakeChromium:
            def launch(self, *, headless: bool) -> FakeBrowser:
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

        class FakePlaywrightContext:
            def __enter__(self) -> FakePlaywright:
                return FakePlaywright()

            def __exit__(self, exc_type, exc_value, traceback) -> None:
                return None

        first_row = AnswerRow(excel_row=2, answers=())
        second_row = AnswerRow(excel_row=3, answers=())
        captcha_result = SubmitResult(excel_row=2, success=False, message="提交被验证码拦截")
        events = []
        with (
            patch("survey_maker.browser.sync_playwright", return_value=FakePlaywrightContext()),
            patch("survey_maker.browser._submit_single", return_value=captcha_result) as submit_single,
        ):
            results = batch_submit(
                "https://v.wjx.cn/vm/example.aspx",
                [first_row, second_row],
                delay=0,
                progress_callback=events.append,
            )

        self.assertEqual(results, [captcha_result])
        submit_single.assert_called_once()
        self.assertEqual([event.stage for event in events], ["starting", "failed", "stopped"])

    def test_batch_continues_after_ordinary_failure_and_reports_all_rows(self) -> None:
        class FakeBrowser:
            def close(self) -> None:
                pass

        class FakeChromium:
            def launch(self, *, headless: bool) -> FakeBrowser:
                return FakeBrowser()

        class FakePlaywright:
            chromium = FakeChromium()

        class FakePlaywrightContext:
            def __enter__(self) -> FakePlaywright:
                return FakePlaywright()

            def __exit__(self, exc_type, exc_value, traceback) -> None:
                return None

        first_row = AnswerRow(excel_row=2, answers=())
        second_row = AnswerRow(excel_row=3, answers=())
        first_result = SubmitResult(excel_row=2, success=False, message="未找到提交按钮。")
        second_result = SubmitResult(excel_row=3, success=True, message="提交成功")
        events = []
        with (
            patch("survey_maker.browser.sync_playwright", return_value=FakePlaywrightContext()),
            patch(
                "survey_maker.browser._submit_single",
                side_effect=[first_result, second_result],
            ) as submit_single,
            patch("survey_maker.browser.time.sleep") as sleep,
        ):
            results = batch_submit(
                "https://v.wjx.cn/vm/example.aspx",
                [first_row, second_row],
                delay=1.5,
                progress_callback=events.append,
            )

        self.assertEqual(results, [first_result, second_result])
        self.assertEqual(submit_single.call_count, 2)
        sleep.assert_called_once_with(1.5)
        self.assertEqual(
            [(event.stage, event.excel_row) for event in events],
            [
                ("starting", 2),
                ("failed", 2),
                ("waiting", 2),
                ("starting", 3),
                ("succeeded", 3),
            ],
        )


if __name__ == "__main__":
    unittest.main()