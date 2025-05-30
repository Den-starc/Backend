from collections import defaultdict
from typing import Callable, Generic, TypeVar

from survey.domain.entities import (
    QuestionStatDict,
    SurveyStatDict,
    SurveyUserStatDict,
    UserStatDict,
)


class StatSurveyFactory:
    @staticmethod
    def _question_factory() -> QuestionStatDict:
        return QuestionStatDict(uuid="", name="", type="", total_count=0, answers=[])

    @staticmethod
    def survey_factory() -> SurveyStatDict:
        return SurveyStatDict(
            uuid="",
            name="",
            status="",
            questions=defaultdict(StatSurveyFactory._question_factory),
        )

    @staticmethod
    def _users_factory() -> UserStatDict:
        return UserStatDict(uuid="", name="", photo="", user_completed_at="", questions=[])

    @staticmethod
    def survey_user_factory() -> SurveyUserStatDict:
        return SurveyUserStatDict(
            uuid="", name="", status="", users=defaultdict(StatSurveyFactory._users_factory)
        )


T = TypeVar("T")


class DefaultDictFactory(Generic[T]):
    @staticmethod
    def create(factory: Callable[[], T]) -> defaultdict[str, T]:
        return defaultdict(factory)
