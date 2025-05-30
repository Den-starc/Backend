from datetime import timedelta

import pytest
from django.urls import reverse
from django.utils import timezone
from profiles.models import CustomUser
from rest_framework import status
from rest_framework.test import APIClient
from survey.models import AnswerOption, Question, Survey, UserAnswer, UserResponse
from survey.services.complete_survey import CompleteSurveyService


@pytest.fixture
def client(username: str) -> APIClient:
    CustomUser.objects.create_user(
        username=username, email=f"{username}@example.com", password="1234"
    )
    client = APIClient()
    client.default_format = "json"
    auth = client.post(reverse("knox_login"), data={"username": username, "password": "1234"})
    token = auth.json()["token"]
    params = {"HTTP_AUTHORIZATION": f"Token {token}"}
    client.credentials(**params)
    return client


@pytest.mark.django_db
class TestSurveyModel:
    def test_complete_survey(self) -> None:
        """Tест на корректность обновления статуса прохождения опроса"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        user = CustomUser.objects.create(
            username="test.user", password="1234", email="test.user@example.com"
        )
        user_response = UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        start_time = timezone.now()
        CompleteSurveyService.complete_survey(survey, user)
        user_response.refresh_from_db()

        assert user_response.status == UserResponse.Status.COMPLETED
        assert start_time < user_response.completed_at < start_time + timedelta(minutes=1)

    @pytest.mark.parametrize("username", ["test.user"])
    def test_add_null_answer_options(self, client: APIClient, username: str) -> None:
        """Тест на корректность добавления пустых ответов под вопросами"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        user_2 = CustomUser.objects.create(username="test.user2", email="test2@example.com")
        survey = Survey.objects.create(
            name="Survey 10", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        survey.owner_user_ids.add(user)

        # Создание вопросов
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 10", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        question_2 = Question.objects.create(
            survey=survey, seq_id=2, name="Вопрос 20", type=Question.QuestionType.TEXT
        )
        question_3 = Question.objects.create(
            survey=survey, seq_id=3, name="Вопрос 30", type=Question.QuestionType.SINGLE_CHOICE
        )
        # Создание ответов под вопросами
        AnswerOption.objects.create(question=question, seq_id=1, name="multichoice option 1")
        multichoice_answer_2 = AnswerOption.objects.create(
            question=question, seq_id=2, name="multichoice option 2"
        )
        singlechoice_answer_1 = AnswerOption.objects.create(
            question=question_2, seq_id=1, name="singlechoice option 1"
        )
        AnswerOption.objects.create(question=question_2, seq_id=2, name="singlechoice option 2")
        AnswerOption.objects.create(question=question_3, seq_id=1, name="text option 1")
        # Создаем запись о прохождении опросов обоих пользователей
        user_response = UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        user_response_2 = UserResponse.objects.create(
            survey=survey, user=user_2, status=UserResponse.Status.IN_PROGRESS
        )

        # Ответы для user
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=multichoice_answer_2,
        )
        UserAnswer.objects.create(
            user_response=user_response, question=question_3, text_answer="text user 1"
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question_2,
            answer_option=singlechoice_answer_1,
        )

        # Ответы для user_2
        UserAnswer.objects.create(
            user_response=user_response_2,
            question=question,
            answer_option=multichoice_answer_2,
        )
        UserAnswer.objects.create(
            user_response=user_response_2, question=question_3, text_answer="text user 2"
        )
        UserAnswer.objects.create(
            user_response=user_response_2,
            question=question_2,
            answer_option=singlechoice_answer_1,
        )

        CompleteSurveyService.complete_survey(survey, user)
        CompleteSurveyService.complete_survey(survey, user_2)

        response = client.get(reverse("survey-stat", kwargs={"uuid": survey.uuid}))

        assert response.status_code == status.HTTP_200_OK

        response_data = response.data
        questions = survey.questions.all()

        assert response_data["uuid"] == str(survey.uuid)
        assert len(response_data["questions"]) == len(questions)
        for i in range(len(response_data["questions"])):
            assert response_data["questions"][i]["uuid"] == str(questions[i].uuid)
            assert len(response_data["questions"][i]["answers"]) == len(questions[i].answers.all())
