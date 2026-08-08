import unittest

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import sync_playwright

from survey_maker.browser import _fill_answer_row
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

        _fill_answer_row(self.page, answer_row)

        self.assertTrue(self.page.locator("#q1_2").is_checked())
        self.assertTrue(self.page.locator("#q2_1").is_checked())
        self.assertTrue(self.page.locator("#q2_2").is_checked())
        self.assertEqual(self.page.locator("textarea[name='q3']").input_value(), "需要补齐服务")


if __name__ == "__main__":
    unittest.main()