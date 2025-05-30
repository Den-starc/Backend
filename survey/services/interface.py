from abc import ABC, abstractmethod

from survey.domain.entities import UserAnswerRequest, UserAnswerUpdate
from survey.models import Question, UserResponse


class QuestionStrategy:
    @abstractmethod
    def handle_answer(self, user_answer_update: UserAnswerUpdate) -> None:
        pass


class ISurveyValidator(ABC):
    @abstractmethod
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        pass


class IQuestionAnswerValidator(ABC):
    @classmethod
    @abstractmethod
    def check(cls, question: Question) -> None:
        pass


class ISurveyChecker(ABC):
    @abstractmethod
    def check(self, user_response: UserResponse | None) -> bool:
        pass
