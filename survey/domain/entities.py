from collections import defaultdict
from dataclasses import dataclass
from enum import StrEnum
from typing import TypedDict

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser

from survey.models import AnswerOption, Question, Survey, UserResponse


@dataclass
class UserAnswerRequest:
    user: AbstractBaseUser | AnonymousUser | None
    survey: Survey
    question: str | None = None
    answer_option: str | None = None
    text_answer: str | None = None
    user_response_uuid: str | None = None


@dataclass
class UserAnswerUpdate:
    user_response: UserResponse
    question: Question
    answer_option: AnswerOption | None = None
    text_answer: str | None = None


class AnswerUserStatDict(TypedDict):
    uuid: str
    name: str


class QuestionUserStatDict(TypedDict):
    uuid: str
    name: str
    type: str
    answers: list[AnswerUserStatDict]


class UserStatDict(TypedDict):
    uuid: str
    name: str
    photo: str
    user_completed_at: str
    questions: list


class SurveyUserStatDict(TypedDict):
    uuid: str
    name: str
    status: str
    users: defaultdict[str, UserStatDict]


class QuestionStatDict(TypedDict):
    uuid: str
    name: str
    type: str
    total_count: int
    answers: list


class SurveyStatDict(TypedDict):
    uuid: str
    name: str
    status: str
    questions: defaultdict[str, QuestionStatDict]


class SurveyFilterAction(StrEnum):
    own = "own"
    all_active = "all_active"
