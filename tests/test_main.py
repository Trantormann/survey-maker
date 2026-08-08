from argparse import Namespace
from contextlib import nullcontext
import unittest
from unittest.mock import patch

import main
from survey_maker.browser import SubmitResult
from survey_maker.excel import AnswerRow, WorkbookValidationError


class SubmitCommandTests(unittest.TestCase):
    def test_validate_keeps_manual_questions_strict(self) -> None:
        arguments = Namespace(url="https://v.wjx.cn/vm/example.aspx", excel="answers.xlsx")
        questions = []
        with (
            patch.object(main, "_load_questions", return_value=questions),
            patch.object(main, "read_answer_rows", return_value=[]) as read_answer_rows,
        ):
            self.assertEqual(main.validate_command(arguments), 0)

        read_answer_rows.assert_called_once_with(arguments.excel, questions)

    def test_prepare_allows_manual_questions(self) -> None:
        arguments = Namespace(
            url="https://v.wjx.cn/vm/example.aspx",
            excel="answers.xlsx",
            row=2,
            authorized=True,
        )
        questions = []
        answer_row = AnswerRow(excel_row=2, answers=())
        with (
            patch.object(main, "_load_questions", return_value=questions),
            patch.object(main, "read_answer_rows", return_value=[answer_row]) as read_answer_rows,
            patch.object(main, "prefilled_browser", return_value=nullcontext(None)),
            patch("builtins.input", return_value=""),
        ):
            self.assertEqual(main.prepare_command(arguments), 0)

        read_answer_rows.assert_called_once_with(
            arguments.excel,
            questions,
            allow_manual_questions=True,
        )

    def test_missing_requested_row_prevents_every_submission(self) -> None:
        arguments = Namespace(
            url="https://v.wjx.cn/vm/example.aspx",
            excel="answers.xlsx",
            authorized=True,
            rows=[2, 99],
            headless=True,
            delay=2.0,
        )
        with (
            patch.object(main, "_load_questions", return_value=[]),
            patch.object(main, "read_answer_rows", return_value=[AnswerRow(excel_row=2, answers=())]),
            patch.object(main, "batch_submit") as batch_submit,
        ):
            with self.assertRaisesRegex(WorkbookValidationError, "99.*不会提交任何行"):
                main.submit_command(arguments)

        batch_submit.assert_not_called()

    def test_selected_rows_are_the_only_rows_submitted(self) -> None:
        row_two = AnswerRow(excel_row=2, answers=())
        row_five = AnswerRow(excel_row=5, answers=())
        arguments = Namespace(
            url="https://v.wjx.cn/vm/example.aspx",
            excel="answers.xlsx",
            authorized=True,
            rows=[5],
            headless=False,
            delay=3.0,
        )
        with (
            patch.object(main, "_load_questions", return_value=[]),
            patch.object(main, "read_answer_rows", return_value=[row_two, row_five]),
            patch.object(
                main,
                "batch_submit",
                return_value=[SubmitResult(excel_row=5, success=True, message="提交成功")],
            ) as batch_submit,
        ):
            self.assertEqual(main.submit_command(arguments), 0)

        batch_submit.assert_called_once_with(
            arguments.url,
            [row_five],
            headless=False,
            delay=3.0,
        )

    def test_submit_parser_rejects_negative_delay(self) -> None:
        parser = main.build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(
                [
                    "submit",
                    "--url",
                    "https://v.wjx.cn/vm/example.aspx",
                    "--excel",
                    "answers.xlsx",
                    "--delay",
                    "-1",
                ]
            )


if __name__ == "__main__":
    unittest.main()