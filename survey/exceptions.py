from typing import Mapping


class ValidateAnswerError(Exception):
    def __init__(
        self, message: str | None, question_error_map: Mapping[str, str] | None = None
    ) -> None:
        self.message = message
        self.question_error_map = question_error_map
