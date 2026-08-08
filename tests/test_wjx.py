import unittest

from survey_maker.wjx import QuestionType, _validate_wjx_url, parse_questions


HTML = """
<fieldset>
  <div class='field ui-field-contain' topic='1' id='div1' req='1' type='3'>
    <div class='field-label'><div class='topichtml'>1. 所在地区</div></div>
    <div class='ui-radio'><input type='radio' value='1' id='q1_1' name='q1'><div class='label'>北京</div></div>
    <div class='ui-radio'><input type='radio' value='2' id='q1_2' name='q1'><div class='label'>上海</div></div>
  </div>
  <div class='field ui-field-contain' topic='2' id='div2' req='0' minvalue='1' maxvalue='2' type='4'>
    <div class='field-label'><div class='topichtml'>2. 感兴趣的主题</div></div>
    <div class='ui-checkbox'><input type='checkbox' value='1' id='q2_1' name='q2'><div class='label'>技术</div></div>
    <div class='ui-checkbox'><input type='checkbox' value='2' id='q2_2' name='q2'><div class='label'>设计</div></div>
  </div>
  <div class='field ui-field-contain' topic='3' id='div3' req='1' type='1'>
    <div class='field-label'><div class='topichtml'>3. 补充说明</div></div>
    <textarea name='q3'></textarea>
  </div>
</fieldset>
"""


class QuestionParserTests(unittest.TestCase):
    def test_parses_question_order_types_and_choices(self) -> None:
        questions = parse_questions(HTML)

        self.assertEqual([question.number for question in questions], [1, 2, 3])
        self.assertEqual(questions[0].title, "1. 所在地区")
        self.assertTrue(questions[0].required)
        self.assertEqual(questions[0].question_type, QuestionType.SINGLE_CHOICE)
        self.assertEqual([choice.label for choice in questions[0].choices], ["北京", "上海"])
        self.assertEqual(questions[1].question_type, QuestionType.MULTIPLE_CHOICE)
        self.assertEqual(questions[1].max_choices, 2)
        self.assertEqual(questions[1].min_choices, 1)
        self.assertEqual(questions[2].question_type, QuestionType.TEXT)
        self.assertEqual(questions[2].field_name, "q3")

    def test_rejects_non_wjx_host(self) -> None:
        with self.assertRaises(ValueError):
            _validate_wjx_url("https://example.com/form")


if __name__ == "__main__":
    unittest.main()