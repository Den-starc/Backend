import operator
from datetime import timedelta
from functools import cache
from uuid import UUID

import pytest
from django.db import IntegrityError
from django.urls import reverse
from django.utils import timezone
from profiles.models import CustomUser
from rest_framework import status
from rest_framework.test import APIClient
from survey.api.serializers import SurveyActions
from survey.api.views import SurveyViewSet
from survey.domain.entities import UserAnswerRequest
from survey.models import AnswerOption, Question, Survey, UserAnswer, UserResponse
from survey.services.complete_survey import CompleteSurveyService

LIST_LENGTH = 11


@cache
def create_multiple_data() -> list[dict]:
    statuses = (Survey.Status.DRAFT, Survey.Status.ACTIVE, Survey.Status.DRAFT)
    is_anonymous_choices = (True, False)

    base_datetime = timezone.now()
    return [
        {
            "name": f"Survey {i}",
            "status": statuses[i % len(statuses)],
            "is_anonymous": is_anonymous_choices[i % len(is_anonymous_choices)],
            "end_date": base_datetime + timedelta(days=i),
        }
        for i in range(LIST_LENGTH)
    ]


def sort_multiple_data_by_field(field: str) -> list[dict]:
    is_reversed = False
    if field[0] == "-":
        field = field[1:]
        is_reversed = True
    data = create_multiple_data()
    data.sort(key=operator.itemgetter(field), reverse=is_reversed)
    return data


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


@pytest.fixture
def generate_user_answers(username: str) -> Survey:
    survey = Survey.objects.create(name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False)
    user, _ = CustomUser.objects.get_or_create(
        username=username, defaults={"email": "test@example.com"}
    )
    survey.owner_user_ids.add(user)
    question = Question.objects.create(
        survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
    )
    question_2 = Question.objects.create(
        survey=survey, seq_id=2, name="Вопрос 2", type=Question.QuestionType.TEXT
    )
    answer_option_1 = AnswerOption.objects.create(
        question=question, seq_id=1, name="Option 1", is_active=True
    )
    AnswerOption.objects.create(question=question, seq_id=2, name="Option 2", is_active=True)
    answer_option_3 = AnswerOption.objects.create(
        question=question, seq_id=3, name="Option 3", is_active=True
    )

    for user_response_status in (UserResponse.Status.COMPLETED, UserResponse.Status.IN_PROGRESS):
        for i in range(5):
            user = CustomUser.objects.create(
                username=f"test.user{i}{user_response_status}",
                password="1234",
                email=f"test.user{i}{user_response_status}@example.com",
            )
            user_response = UserResponse.objects.create(
                survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
            )
            UserAnswer.objects.create(
                user_response=user_response,
                question=question,
                answer_option=answer_option_1,
            )
            UserAnswer.objects.create(
                user_response=user_response,
                question=question,
                answer_option=answer_option_3,
            )
            UserAnswer.objects.create(
                user_response=user_response,
                question=question_2,
                text_answer=f"Тестовый текст от пользователя {i}",
            )
            if user_response_status == UserResponse.Status.COMPLETED:
                CompleteSurveyService.complete_survey(survey, user)

    return survey


@pytest.mark.django_db
class TestSurveyViewSet:
    AMOUNT_SURVEYS = 1

    @staticmethod
    def _list_url() -> str:
        return reverse("survey-list")

    @staticmethod
    def _get_detail_url(uuid: UUID) -> str:
        return reverse("survey-detail", kwargs={"uuid": uuid})

    @pytest.mark.parametrize("username", ["test.user"])
    def test_list_survey(self, client: APIClient, username: str) -> None:
        """Тест получения списка опросов"""
        data = create_multiple_data()
        user = CustomUser.objects.get(username=username)
        for item in data:
            survey = Survey.objects.create(**item)
            survey.owner_user_ids.add(user)

        response = client.get(self._list_url())

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["results"]
        assert response.json()["count"] == len(data)

    @pytest.mark.parametrize("username", ["test.user"])
    def test_list_survey_all_active(self, client: APIClient, username: str) -> None:
        """Тест получения списка опросов доступных для прохождения"""
        data = create_multiple_data()
        survey_active_count = 0
        for item in data:
            Survey.objects.create(**item)
            if item.get("status") == Survey.Status.ACTIVE:
                survey_active_count += 1

        response = client.get(self._list_url(), data={"filter_action": "all_active"})

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["results"]
        assert response.json()["count"] == survey_active_count

    @pytest.mark.parametrize("username", ["test.user"])
    def test_list_survey_not_archived(self, client: APIClient, username: str) -> None:
        """Тест отсутствия архивированных опросов"""
        user = CustomUser.objects.get(username=username)
        survey = Survey.objects.create(name="Survey", status=Survey.Status.ARCHIVED)
        survey.owner_user_ids.add(user)

        response = client.get(self._list_url())

        assert response.status_code == status.HTTP_200_OK
        assert not response.json()["results"]
        assert response.json()["count"] == 0

    @pytest.mark.parametrize(
        ("username", "progress", "survey_status", "survey_actions", "result", "is_user_owner"),
        [
            (
                "test1.user",
                UserResponse.Status.IN_PROGRESS,
                Survey.Status.DRAFT,
                [SurveyActions.active, SurveyActions.delete],
                status.HTTP_200_OK,
                False,
            ),
            (
                "test1.user",
                UserResponse.Status.IN_PROGRESS,
                Survey.Status.ACTIVE,
                [SurveyActions.close, SurveyActions.get_stat, SurveyActions.delete],
                status.HTTP_200_OK,
                True,
            ),
            (
                "test.user2",
                UserResponse.Status.COMPLETED,
                Survey.Status.CLOSED,
                [SurveyActions.delete, SurveyActions.get_stat],
                status.HTTP_200_OK,
                True,
            ),
            (
                "test.user2",
                UserResponse.Status.COMPLETED,
                Survey.Status.ARCHIVED,
                [SurveyActions.get_stat],
                status.HTTP_200_OK,
                True,
            ),
        ],
    )
    def test_retrieve_survey_ok(
        self,
        client: APIClient,
        username: str,
        progress: str,
        result: int,
        is_user_owner: bool,
        survey_status: str,
        survey_actions: list[SurveyActions],
    ) -> None:
        """Тест успешного получения конкретного опроса"""
        survey = Survey.objects.create(name="Survey 1", is_anonymous=False)
        survey.status = survey_status
        survey.save()
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey.owner_user_ids.set([user.pk] if is_user_owner else [])
        UserResponse.objects.create(survey=survey, user=user, status=progress)
        response = client.get(self._get_detail_url(survey.uuid))
        assert response.status_code == result
        assert response.json()["isUserOwner"] == is_user_owner
        assert response.json()["actions"] == [
            {"name": action.name, "label": action.value} for action in survey_actions
        ]

    @pytest.mark.parametrize(
        ("is_anonymous", "result"),
        [(True, status.HTTP_200_OK), (False, status.HTTP_401_UNAUTHORIZED)],
    )
    def test_retrieve_anonymous_user_survey(self, is_anonymous: bool, result: int) -> None:
        """Тест получения конкретного опроса анонимным пользователем"""
        client = APIClient()
        client.default_format = "json"
        survey = Survey.objects.create(
            name="Survey 1", is_anonymous=is_anonymous, status=Survey.Status.ACTIVE
        )
        survey.save()
        response = client.get(self._get_detail_url(survey.uuid))

        assert response.status_code == result

    @pytest.mark.parametrize(
        ("username", "progress", "result"),
        [
            ("test.user2", UserResponse.Status.COMPLETED, status.HTTP_400_BAD_REQUEST),
        ],
    )
    def test_retrieve_survey_bad_request(
        self, client: APIClient, username: str, progress: str, result: int
    ) -> None:
        """Тест неуспешного получения конкретного опроса"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        UserResponse.objects.create(survey=survey, user=user, status=progress)
        response = client.get(self._get_detail_url(survey.uuid))
        assert response.status_code == result

    @pytest.mark.parametrize("username", ["test.user"])
    def test_retrieve_survey_without_user_response(self, client: APIClient, username: str) -> None:
        """Тест обработки ошибки при отсутствии userresponse у пользователя"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        response = client.get(self._get_detail_url(survey.uuid))
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("username", ["test.user"])
    def test_retrieve_manager_survey(self, client: APIClient, username: str) -> None:
        """Тест отображения опроса для владельца прошедшего опрос"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        UserResponse.objects.create(survey=survey, user=user, status="COMPLETE")
        response = client.get(self._get_detail_url(survey.uuid))
        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("username", ["test.user"])
    def test_filter_by_name(self, client: APIClient, username: str) -> None:
        """Тест фильтрации по имени"""
        data = create_multiple_data()
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        for item in data:
            survey = Survey.objects.create(**item)
            survey.owner_user_ids.add(user)
        response = client.get(self._list_url(), {"name": "Survey 1"})
        filtered_values_count = 2
        assert response.status_code == status.HTTP_200_OK
        assert len(response.json()["results"]) == filtered_values_count
        assert response.json()["results"][0]["name"] == "Survey 1"
        assert response.json()["results"][1]["name"] == "Survey 10"

    @pytest.mark.parametrize("username", ["test.user"])
    @pytest.mark.parametrize(
        "sort_field",
        [prefix + field for field in SurveyViewSet.ordering_fields for prefix in ("", "-")],
    )
    def test_sort(self, client: APIClient, username: str, sort_field: str) -> None:
        """Тест на проверку сортировки"""
        data = create_multiple_data()
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        for item in data:
            survey = Survey.objects.create(**item)
            survey.owner_user_ids.add(user)
        response = client.get(self._list_url(), {"ordering": sort_field, "pageSize": LIST_LENGTH})
        assert response.status_code == status.HTTP_200_OK
        assert [item["name"] for item in response.json()["results"]] == [
            item["name"] for item in sort_multiple_data_by_field(sort_field)
        ]

    @pytest.mark.parametrize("username", ["test.user"])
    def test_json_response_survey(self, client: APIClient, username: str) -> None:
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        response = client.get(self._get_detail_url(survey.uuid))
        data = response.json()

        assert response.status_code == status.HTTP_200_OK
        assert data["name"] == "Survey 1"
        assert data["status"] == "active"
        assert not data["isAnonymous"]
        assert not data["isUserOwner"]
        assert not data["userAnswers"]

    @pytest.mark.parametrize(("username", "result"), [("test.user", True), ("test.user1", False)])
    def test_is_user_owner(self, client: APIClient, username: str, result: bool) -> None:
        """Тест на валидацию пользователя прав владельца"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        test_user_1, _ = CustomUser.objects.get_or_create(
            username="test.user", defaults={"email": "test@example.com"}
        )
        survey.owner_user_ids.set([test_user_1])
        response = client.get(self._get_detail_url(survey.uuid))

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["isUserOwner"] is result

    @pytest.mark.parametrize(
        ("username", "status_code"),
        [("test.user", status.HTTP_200_OK), ("test.user1", status.HTTP_400_BAD_REQUEST)],
    )
    def test_is_owner_user_passed_survey(
        self, client: APIClient, username: str, status_code: int
    ) -> None:
        """Тест на возможность просмотра опроса после прохождения пользователем"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        user_owner, _ = CustomUser.objects.get_or_create(
            username="test.user", defaults={"email": "test@example.com"}
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey.owner_user_ids.set([user_owner])
        UserResponse.objects.create(survey=survey, user=user, status=UserResponse.Status.COMPLETED)
        response = client.get(self._get_detail_url(survey.uuid))

        assert response.status_code == status_code

    @pytest.mark.parametrize("username", ["test.user"])
    def test_pagination(self, client: APIClient, username: str) -> None:
        """Тест на проверку пагинации"""
        Survey.objects.create(name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False)
        response = client.get(self._list_url())
        data = response.json()
        assert response.status_code == status.HTTP_200_OK
        assert "results" in data
        assert "count" in data

    @pytest.mark.parametrize("username", ["test.user"])
    def test_create_survey(self, client: APIClient, username: str) -> None:
        """Тест создания опроса"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        data = {
            "name": "Survey 2",
            "status": Survey.Status.DRAFT,
            "end_date": timezone.now().isoformat(),
            "owner_user_ids": [user.id],
            "is_anonymous": False,
        }
        response = client.post(self._list_url(), data)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == data["name"]
        assert Survey.objects.count() == self.AMOUNT_SURVEYS

    @pytest.mark.parametrize("username", ["test.user"])
    def test_delete_survey(self, client: APIClient, username: str) -> None:
        """Тест удаления опроса"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        response = client.delete(self._get_detail_url(survey.uuid))
        assert response.status_code == status.HTTP_405_METHOD_NOT_ALLOWED

    @pytest.mark.parametrize("username", ["test.user"])
    @pytest.mark.parametrize(
        "new_status, status_code, error_msg",
        [
            (Survey.Status.ACTIVE, status.HTTP_400_BAD_REQUEST, "Вопросы не должны повторяться"),
            (Survey.Status.DRAFT, status.HTTP_200_OK, None),
            (Survey.Status.CLOSED, status.HTTP_200_OK, None),
        ],
    )
    def test_create_repeat_question(
        self,
        client: APIClient,
        username: str,
        new_status: Survey.Status,
        status_code: int,
        error_msg: str | None,
    ) -> None:
        """Тест на создание answer-option с уже существующим названием"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        question_1 = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.SINGLE_CHOICE
        )
        AnswerOption.objects.create(question=question_1, seq_id=1, name="Option 1", is_active=True)
        question_2 = Question.objects.create(
            survey=survey, seq_id=2, name="Вопрос 1", type=Question.QuestionType.SINGLE_CHOICE
        )
        AnswerOption.objects.create(question=question_2, seq_id=1, name="Option 2", is_active=True)
        response = client.patch(self._get_detail_url(survey.uuid), data={"status": new_status})
        assert response.status_code == status_code
        assert response.data.get("error") == error_msg

    @pytest.mark.parametrize("username", ["test.user"])
    @pytest.mark.parametrize(
        "new_status, status_code, error_msg",
        [
            (
                Survey.Status.ACTIVE,
                status.HTTP_400_BAD_REQUEST,
                "Варианты ответов не должны повторяться",
            ),
            (Survey.Status.DRAFT, status.HTTP_200_OK, None),
            (Survey.Status.CLOSED, status.HTTP_200_OK, None),
        ],
    )
    def test_create_repeat_answer_option(
        self,
        client: APIClient,
        username: str,
        new_status: Survey.Status,
        status_code: int,
        error_msg: str,
    ) -> None:
        """Тест на создание answer-option с уже существующим названием"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.SINGLE_CHOICE
        )
        AnswerOption.objects.create(question=question, seq_id=1, name="Option 1", is_active=True)
        AnswerOption.objects.create(question=question, seq_id=2, name="Option 1", is_active=True)
        response = client.patch(self._get_detail_url(survey.uuid), data={"status": new_status})
        assert response.status_code == status_code
        assert response.data.get("error") == error_msg


@pytest.mark.django_db
class TestQuestionViewSet:
    AMOUNT_QUESTION = 1

    @staticmethod
    def _list_url() -> str:
        return reverse("question-list")

    @staticmethod
    def _get_detail_url(uuid: str) -> str:
        return reverse("question-detail", kwargs={"pk": uuid})

    @pytest.mark.parametrize("username", ["test.user"])
    def test_create_question(self, client: APIClient, username: str) -> None:
        """Тест на создание нового вопроса"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        data = {
            "survey": survey.uuid,
            "seq_id": 1,
            "name": "Вопрос 1",
            "type": Question.QuestionType.SINGLE_CHOICE,
            "is_active": True,
        }
        response = client.post(self._list_url(), data)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == data["name"]
        assert Question.objects.count() == self.AMOUNT_QUESTION

    @pytest.mark.parametrize("username", ["test.user"])
    def test_update_question(self, client: APIClient, username: str) -> None:
        """Тест на обновление существующего вопроса"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.SINGLE_CHOICE
        )
        data = {
            "survey": survey.uuid,
            "seq_id": 1,
            "name": "Updated Question",
            "type": Question.QuestionType.MULTIPLE_CHOICE,
            "is_active": True,
        }
        response = client.put(self._get_detail_url(question.uuid), data)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Question"
        assert response.data["type"] == Question.QuestionType.MULTIPLE_CHOICE

    @pytest.mark.parametrize("username", ["test.user"])
    def test_update_question_type_to_text(self, client: APIClient, username: str) -> None:
        """Тест на обновление существующего вопроса на текстовый тип"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.SINGLE_CHOICE
        )
        AnswerOption.objects.bulk_create(
            [
                AnswerOption(question=question, seq_id=1, name="Option 1", is_active=True),
                AnswerOption(question=question, seq_id=2, name="Option 2", is_active=True),
            ]
        )
        response = client.patch(
            self._get_detail_url(question.uuid), data={"type": Question.QuestionType.TEXT}
        )
        assert response.status_code == status.HTTP_200_OK
        assert not AnswerOption.objects.filter(question=question).exists()
        assert response.data["type"] == Question.QuestionType.TEXT

    @pytest.mark.parametrize("username", ["test.user"])
    def test_delete_question(self, client: APIClient, username: str) -> None:
        """Тест на удаление вопроса"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.SINGLE_CHOICE
        )
        response = client.delete(self._get_detail_url(question.uuid))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert Question.objects.count() == self.AMOUNT_QUESTION - 1

    @pytest.mark.parametrize("username", ["test.user"])
    def test_creare_incorrect_question(self, client: APIClient, username: str) -> None:
        """Тест на проверку связи уникальности seq_id"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.SINGLE_CHOICE
        )
        with pytest.raises(
            IntegrityError,
            match="UNIQUE constraint failed: survey_question.survey_id, survey_question.seq_id",
        ):
            Question.objects.create(
                survey=survey, seq_id=1, name="Вопрос 2", type=Question.QuestionType.MULTIPLE_CHOICE
            )


@pytest.mark.django_db
class TestAnswerOptionViewSet:
    AMOUNT_OPTIONS = 1

    @staticmethod
    def _list_url() -> str:
        return reverse("answer-option-list")

    @staticmethod
    def _get_detail_url(uuid: UUID) -> str:
        return reverse("answer-option-detail", kwargs={"pk": uuid})

    @pytest.mark.parametrize("username", ["test.user"])
    def test_create_answer_option(self, client: APIClient, username: str) -> None:
        """Тест на создание нового answer-option"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.SINGLE_CHOICE
        )
        AnswerOption.objects.create(question=question, seq_id=1, name="Option 1", is_active=True)
        data = {"question": question.pk, "seq_id": 2, "name": "Option 2", "is_active": True}
        response = client.post(self._list_url(), data)
        assert response.status_code == status.HTTP_201_CREATED
        assert response.data["name"] == data["name"]
        assert AnswerOption.objects.count() == self.AMOUNT_OPTIONS + 1

    @pytest.mark.parametrize("username", ["test.user"])
    def test_update_answer_option(self, client: APIClient, username: str) -> None:
        """Тест на обновление существующего answer-option"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.SINGLE_CHOICE
        )
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=1, name="Option 1", is_active=True
        )
        data = {
            "question": question.uuid,
            "seq_id": 1,
            "name": "Updated Option",
            "is_active": True,
        }
        response = client.put(self._get_detail_url(answer_option.uuid), data)
        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Updated Option"

    @pytest.mark.parametrize("username", ["test.user"])
    def test_delete_answer_option(self, client: APIClient, username: str) -> None:
        """Тест на удаление answer-option"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.SINGLE_CHOICE
        )
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=1, name="Option 1", is_active=True
        )
        response = client.delete(self._get_detail_url(answer_option.uuid))
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert AnswerOption.objects.count() == self.AMOUNT_OPTIONS - 1

    @pytest.mark.parametrize("username", ["test.user"])
    def test_create_invalid_answer_option(self, client: APIClient, username: str) -> None:
        """Тест на обработку ошибки при создании некорректного answer option"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.SINGLE_CHOICE
        )
        AnswerOption.objects.create(question=question, seq_id=1, name="Option 1", is_active=True)
        with pytest.raises(
            IntegrityError,
            match="UNIQUE constraint failed: survey_answeroption.question_id, "
            "survey_answeroption.seq_id",
        ):
            AnswerOption.objects.create(
                question=question, seq_id=1, name="Option 2", is_active=True
            )


@pytest.mark.django_db
class TestUserAnswerViewSet:
    AMOUNT_ANSWERS = 1

    @staticmethod
    def _list_url() -> str:
        return reverse("user-answer-list")

    @staticmethod
    def _get_detail_url(uuid: UUID) -> str:
        return reverse("user-answer-detail", kwargs={"uuid": uuid})

    def test_list_unauthorized(
        self,
    ) -> None:
        """Тест на недоступность данных user answer неавторизованным пользователям"""
        client = APIClient()
        client.default_format = "json"
        response = client.get(self._list_url())
        assert response.status_code == status.HTTP_401_UNAUTHORIZED

    @pytest.mark.parametrize("username", ["test.user"])
    @pytest.mark.parametrize("create_user_response", [True, False])
    def test_create_user_answer(
        self, client: APIClient, username: str, create_user_response: bool
    ) -> None:
        """Тест на создание user answer"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        new_answer_option = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 1", is_active=True
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        if create_user_response:
            UserResponse.objects.create(
                survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
            )
        data = {
            "survey": survey.uuid,
            "question": question.pk,
            "answer_option": new_answer_option.pk,
        }
        response = client.post(self._list_url(), data)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert UserAnswer.objects.count() == self.AMOUNT_ANSWERS
        assert UserResponse.objects.get(
            status=UserResponse.Status.IN_PROGRESS, survey=survey, user=user
        )

    def test_create_anonymous_user_answer(self) -> None:
        """Тест на создание anonymous user answer"""
        client = APIClient()
        client.default_format = "json"
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=True
        )
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        new_answer_option = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 1", is_active=True
        )

        data = {
            "survey": survey.uuid,
            "question": question.pk,
            "answer_option": new_answer_option.pk,
        }
        response = client.post(self._list_url(), data)
        cookie_user_response_uuid = response.cookies.get("user_response_uuid")
        user_response_uuid = cookie_user_response_uuid.value if cookie_user_response_uuid else None

        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert user_response_uuid
        assert UserAnswer.objects.count() == self.AMOUNT_ANSWERS
        assert UserResponse.objects.get(
            uuid=user_response_uuid, status=UserResponse.Status.IN_PROGRESS, survey=survey
        )

    @pytest.mark.parametrize("username", ["test.user"])
    @pytest.mark.parametrize(
        "survey_status", [Survey.Status.DRAFT, Survey.Status.CLOSED, Survey.Status.ARCHIVED]
    )
    def test_create_user_answer_for_not_active_survey(
        self, client: APIClient, username: str, survey_status: Survey.Status
    ) -> None:
        """Тест на проверку ошибки создания text answer при неактивном опросе"""
        survey = Survey.objects.create(name="Survey 1", is_anonymous=False)
        survey.status = survey_status
        survey.save()
        question = Question.objects.create(
            survey=survey,
            seq_id=3,
            name="Question 1",
            type=Question.QuestionType.TEXT,
        )
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 1", is_active=True
        )
        data = {
            "survey": survey.uuid,
            "question": question.pk,
            "answer_option": answer_option.uuid,
        }
        response = client.post(self._list_url(), data)
        assert response.data.get("error") == "Опрос не активен"
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert UserAnswer.objects.count() == 0

    @pytest.mark.parametrize("username", ["test.user"])
    def test_create_user_answer_text_wrong_type(self, client: APIClient, username: str) -> None:
        """Тест на проверку ошибки создания неправильного text answer"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        question = Question.objects.create(
            survey=survey,
            seq_id=3,
            name="Question 1",
            type=Question.QuestionType.TEXT,
        )
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 1", is_active=True
        )
        data = {
            "survey": survey.uuid,
            "question": question.pk,
            "answer_option": answer_option.uuid,
        }
        response = client.post(self._list_url(), data)
        assert response.data.get("error") == "Ответ должен содержать текст"
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert UserAnswer.objects.count() == 0

    @pytest.mark.parametrize("username", ["test.user"])
    @pytest.mark.parametrize("create_answer", [True, False])
    def test_create_user_answer_text_empty(
        self, client: APIClient, username: str, create_answer: bool
    ) -> None:
        """Тест на проверку удаления user answer, если новый текст ответа пустой"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        question = Question.objects.create(
            survey=survey,
            seq_id=1,
            name="Question 1",
            type=Question.QuestionType.TEXT,
        )
        data = {
            "survey": survey.uuid,
            "question": question.pk,
            "text_answer": "",
        }
        if create_answer:
            # Создание ответа на текстовый вопрос
            client.post(self._list_url(), data | {"text_answer": "TEXT"})
        # Удаление текста ответа на текстовый вопрос
        response = client.post(self._list_url(), data)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert not question.answers.exists()

    @pytest.mark.parametrize("username", ["test.user"])
    @pytest.mark.parametrize("create_answer", [True, False])
    def test_create_user_answer_text_correct_type(
        self, client: APIClient, username: str, create_answer: bool
    ) -> None:
        """Тест на проверку корректного создания user answer"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        question = Question.objects.create(
            survey=survey,
            seq_id=3,
            name="Question 1",
            type=Question.QuestionType.TEXT,
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        data = {
            "survey": survey.uuid,
            "question": question.pk,
            "text_answer": "TEXT",
        }
        if create_answer:
            # Создание ответа на текстовый вопрос
            client.post(self._list_url(), data | {"text_answer": "initial text"})
        response = client.post(self._list_url(), data)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert UserAnswer.objects.count() == self.AMOUNT_ANSWERS

    @pytest.mark.parametrize("username", ["test.user"])
    def test_create_user_answer_single_choice_wrong_type(
        self, client: APIClient, username: str
    ) -> None:
        """Тест на проверку ошибки создания single choice user answer"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        question = Question.objects.create(
            survey=survey,
            seq_id=1,
            name="Question 1",
            type=Question.QuestionType.SINGLE_CHOICE,
        )
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 1", is_active=True
        )
        data = {
            "survey": survey.uuid,
            "question": question.uuid,
            "answer_option": answer_option.uuid,
            "text_answer": "TEXT",
        }
        response = client.post(self._list_url(), data)
        assert response.data.get("error") == "Ответ должен содержать выбранный вариант"
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert UserAnswer.objects.count() == 0

    @pytest.mark.parametrize("username", ["test.user"])
    def test_create_user_answer_single_choice_correct_type(
        self, client: APIClient, username: str
    ) -> None:
        """Тест на проверку корректного создания single choice answer"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        question = Question.objects.create(
            survey=survey,
            seq_id=1,
            name="Question 1",
            type=Question.QuestionType.SINGLE_CHOICE,
        )
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 1", is_active=True
        )
        data = {
            "survey": survey.uuid,
            "question": question.uuid,
            "answer_option": answer_option.uuid,
        }
        response = client.post(self._list_url(), data)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert UserAnswer.objects.count() == self.AMOUNT_ANSWERS

    @pytest.mark.parametrize("username", ["test.user"])
    def test_create_user_answer_multi_choice_wrong_type(
        self, client: APIClient, username: str
    ) -> None:
        """Тест на проверку ошибки создания multichoice user answer"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        question = Question.objects.create(
            survey=survey,
            seq_id=1,
            name="Question 1",
            type=Question.QuestionType.MULTIPLE_CHOICE,
        )
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 1", is_active=True
        )
        data = {
            "survey": survey.uuid,
            "question": question.uuid,
            "answer_option": answer_option.uuid,
            "text_answer": "TEXT",
        }
        response = client.post(self._list_url(), data)
        assert response.data.get("error") == "Ответ должен содержать выбранный вариант"
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert UserAnswer.objects.count() == 0

    @pytest.mark.parametrize("username", ["test.user"])
    def test_create_user_answer_multi_choice_correct_type(
        self, client: APIClient, username: str
    ) -> None:
        """Тест на корректное создание multichoice user answer"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        question = Question.objects.create(
            survey=survey,
            seq_id=1,
            name="Question 1",
            type=Question.QuestionType.MULTIPLE_CHOICE,
        )
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 1", is_active=True
        )
        data = {
            "survey": survey.uuid,
            "question": question.uuid,
            "answer_option": answer_option.uuid,
        }
        response = client.post(self._list_url(), data)
        assert response.status_code == status.HTTP_204_NO_CONTENT
        assert UserAnswer.objects.count() == self.AMOUNT_ANSWERS


@pytest.mark.django_db
class TestCompleteSurvey:
    USER_RESPONSES_COUNT = 10

    @staticmethod
    def _list_url() -> str:
        return reverse("survey-list")

    @staticmethod
    def _complete_url(uuid: UUID) -> str:
        return reverse("survey-complete", kwargs={"uuid": uuid})

    @staticmethod
    def _get_stat_url(uuid: str) -> str:
        return reverse("survey-stat", kwargs={"uuid": uuid})

    @staticmethod
    def _get_user_stat_url(uuid: str) -> str:
        return reverse("survey-stat-user", kwargs={"uuid": uuid})

    @staticmethod
    def _get_detail_url(uuid: str) -> str:
        return reverse("survey-detail", kwargs={"uuid": uuid})

    @pytest.mark.parametrize("username", ["test.user"])
    def test_finish_complete_survey(self, client: APIClient, username: str) -> None:
        """Тест на ошибку прохождения завершенного опроса"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        UserResponse.objects.create(survey=survey, user=user, status=UserResponse.Status.COMPLETED)
        response = client.post(self._complete_url(survey.uuid))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get("error") == "Опрос уже пройден"

    @pytest.mark.parametrize("username", ["test.user"])
    def test_success_anonymous_survey(self, username: str) -> None:
        """Тест завершения опроса анонимным пользователем"""
        client = APIClient()
        client.default_format = "json"
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=True
        )

        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=1, name="Option 1", is_active=True
        )
        answer_option_2 = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 2", is_active=True
        )
        user_response = UserResponse.objects.create(
            survey=survey, status=UserResponse.Status.IN_PROGRESS
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=answer_option,
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=answer_option_2,
        )
        client.cookies["user_response_uuid"] = str(user_response.uuid)
        response = client.post(self._complete_url(survey.uuid))

        assert response.status_code == status.HTTP_200_OK
        assert not response.json()["canFinish"]

    @pytest.mark.parametrize("username", ["test.user"])
    def test_success_survey_data(self, client: APIClient, username: str) -> None:
        """Тест на проверку данных завершенного опроса"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        survey.owner_user_ids.add(user)
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=1, name="Option 1", is_active=True
        )
        answer_option_2 = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 2", is_active=True
        )
        user_response = UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.COMPLETED
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=answer_option,
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=answer_option_2,
        )
        response = client.get(self._get_detail_url(survey.uuid))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["name"] == "Survey 1"
        assert response.data["status"] == "active"
        assert len(response.data["owner_user_ids"]) == 1
        user_answer_count = 2
        assert len(response.data["user_answers"]) == user_answer_count

    @pytest.mark.parametrize("username", ["test.user"])
    def test_survey_state_data(self, client: APIClient, username: str) -> None:
        """Тест на отображение состояния прохождения при передаче номера сессии"""
        client = APIClient()
        client.default_format = "json"
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=True
        )
        survey.owner_user_ids.add(user)
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=1, name="Option 1", is_active=True
        )
        answer_option_2 = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 2", is_active=True
        )
        user_response = UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=answer_option,
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=answer_option_2,
        )

        user_answers_count = 2
        client.cookies["user_response_uuid"] = str(user_response.uuid)
        response = client.get(self._get_detail_url(survey.uuid))
        response_data = response.json()

        assert response.status_code == status.HTTP_200_OK
        assert response_data["name"] == "Survey 1"
        assert response_data["questions"][0]["name"] == "Вопрос 1"
        assert len(response_data["userAnswers"]) == user_answers_count
        assert response_data["userAnswers"][0]["answerOption"] == str(answer_option.uuid)
        assert response_data["userAnswers"][1]["answerOption"] == str(answer_option_2.uuid)

    @pytest.mark.parametrize("username", ["test.user"])
    def test_complete_all_answers_error(self, client: APIClient, username: str) -> None:
        """Тест на проверку заполнения всех ответов (Выдавать ошибку)"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        survey.owner_user_ids.add(user)
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        question_2 = Question.objects.create(
            survey=survey, seq_id=2, name="Вопрос 2", type=Question.QuestionType.SINGLE_CHOICE
        )
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=1, name="Option 1", is_active=True
        )
        answer_option_2 = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 2", is_active=True
        )
        AnswerOption.objects.create(question=question_2, seq_id=1, name="Option 1", is_active=True)
        user_response = UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=answer_option,
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=answer_option_2,
        )
        response = client.post(self._complete_url(survey.uuid))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get("error") == "Ошибка валидации ответов"

    @pytest.mark.parametrize("username", ["test.user"])
    def test_error_empty_user_response(self, client: APIClient, username: str) -> None:
        """Тест на обработку ошибки отсутствия user response"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        survey.owner_user_ids.add(user)
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        question_2 = Question.objects.create(
            survey=survey, seq_id=2, name="Вопрос 2", type=Question.QuestionType.SINGLE_CHOICE
        )
        AnswerOption.objects.create(question=question, seq_id=1, name="Option 1", is_active=True)
        AnswerOption.objects.create(question=question, seq_id=2, name="Option 2", is_active=True)
        AnswerOption.objects.create(question=question_2, seq_id=1, name="Option 1", is_active=True)
        response = client.post(self._complete_url(survey.uuid))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get("error") == "Пользователь не начал проходить опрос"

    @pytest.mark.parametrize("username", ["test.user"])
    def test_empty_answer_singlechoice_option(self, client: APIClient, username: str) -> None:
        """Тест на ошибку пустого singlechoice поля"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        survey.owner_user_ids.add(user)
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        question_2 = Question.objects.create(
            survey=survey, seq_id=2, name="Вопрос 2", type=Question.QuestionType.SINGLE_CHOICE
        )
        AnswerOption.objects.create(question=question, seq_id=1, name="Option 1")
        answer_option_2 = AnswerOption.objects.create(question=question, seq_id=2, name="Option 2")
        AnswerOption.objects.create(question=question_2, seq_id=1, name="Option 1")
        user_response = UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=answer_option_2,
        )
        response = client.post(self._complete_url(survey.uuid))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get("error") == "Ошибка валидации ответов"

    @pytest.mark.parametrize("username", ["test.user"])
    def test_many_answer_singlechoice_option(self, client: APIClient, username: str) -> None:
        """Тест на ошибку нескольких ответов singlechoice поля"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        survey.owner_user_ids.add(user)
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        question_2 = Question.objects.create(
            survey=survey, seq_id=2, name="Вопрос 2", type=Question.QuestionType.SINGLE_CHOICE
        )
        AnswerOption.objects.create(question=question, seq_id=1, name="Option 1")
        answer_option = AnswerOption.objects.create(question=question, seq_id=2, name="Option 2")
        answer_option_2 = AnswerOption.objects.create(
            question=question_2, seq_id=1, name="Option 1"
        )
        answer_option_3 = AnswerOption.objects.create(
            question=question_2, seq_id=2, name="option 2"
        )
        user_response = UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=answer_option,
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question_2,
            answer_option=answer_option_2,
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question_2,
            answer_option=answer_option_3,
        )

        response = client.post(self._complete_url(survey.uuid))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get("error") == "Ошибка валидации ответов"

    @pytest.mark.parametrize("username", ["test.user"])
    def test_empty_answer_multichoice_option(self, client: APIClient, username: str) -> None:
        """Тест на ошибку пустого multichoice поля"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        survey.owner_user_ids.add(user)
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        question_2 = Question.objects.create(
            survey=survey, seq_id=2, name="Вопрос 2", type=Question.QuestionType.SINGLE_CHOICE
        )
        AnswerOption.objects.create(question=question, seq_id=1, name="Option 1")
        AnswerOption.objects.create(question=question, seq_id=2, name="Option 2")
        answer_option_2 = AnswerOption.objects.create(
            question=question_2, seq_id=1, name="Option 1"
        )
        AnswerOption.objects.create(question=question_2, seq_id=2, name="option 2")
        user_response = UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question_2,
            answer_option=answer_option_2,
        )

        response = client.post(self._complete_url(survey.uuid))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get("error") == "Ошибка валидации ответов"

    @pytest.mark.parametrize("username", ["test.user"])
    def test_empty_answer_text_option(self, client: APIClient, username: str) -> None:
        """Тест на ошибку отсутствия text поля"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        survey.owner_user_ids.add(user)
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        question_2 = Question.objects.create(
            survey=survey, seq_id=2, name="Вопрос 2", type=Question.QuestionType.TEXT
        )
        AnswerOption.objects.create(question=question, seq_id=1, name="Option 1", is_active=True)
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 2", is_active=True
        )
        AnswerOption.objects.create(question=question_2, seq_id=1, name="Option 1", is_active=True)
        user_response = UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=answer_option,
        )
        response = client.post(self._complete_url(survey.uuid))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get("error") == "Ошибка валидации ответов"

    @pytest.mark.parametrize("username", ["test.user"])
    def test_null_answer_text_option(self, client: APIClient, username: str) -> None:
        """Тест на ошибку пустого text поля"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        survey.owner_user_ids.add(user)
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        question_2 = Question.objects.create(
            survey=survey, seq_id=2, name="Вопрос 2", type=Question.QuestionType.TEXT
        )
        AnswerOption.objects.create(question=question, seq_id=1, name="Option 1", is_active=True)
        answer_option = AnswerOption.objects.create(
            question=question, seq_id=2, name="Option 2", is_active=True
        )
        user_response = UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        UserAnswer.objects.create(
            user_response=user_response,
            question=question,
            answer_option=answer_option,
        )
        UserAnswer.objects.create(user_response=user_response, question=question_2, text_answer="")
        response = client.post(self._complete_url(survey.uuid))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get("error") == "Ошибка валидации ответов"

    @pytest.mark.parametrize("username", ["test.user10"])
    def test_mapping_error_survey(self, client: APIClient, username: str) -> None:
        """Тест на правильность маппинга ошибки типа поля"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test10@example.com"}
        )
        survey = Survey.objects.create(
            name="Survey 10", status=Survey.Status.ACTIVE, is_anonymous=False
        )
        survey.owner_user_ids.add(user)
        multichoice_question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 10", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        text_question = Question.objects.create(
            survey=survey, seq_id=2, name="Вопрос 20", type=Question.QuestionType.TEXT
        )
        singlechoice_question = Question.objects.create(
            survey=survey, seq_id=3, name="Вопрос 30", type=Question.QuestionType.SINGLE_CHOICE
        )
        AnswerOption.objects.create(
            question=multichoice_question, seq_id=1, name="Multichoice option 1"
        )
        AnswerOption.objects.create(
            question=multichoice_question, seq_id=2, name="Multichoice option 2"
        )
        AnswerOption.objects.create(
            question=singlechoice_question, seq_id=1, name="Singlechoice option 1"
        )
        AnswerOption.objects.create(question=text_question, seq_id=1, name="text option 1")
        UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )
        response = client.post(self._complete_url(survey.uuid))
        questions_error_map = response.data["questions_error_map"]

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert "questions_error_map" in response.data
        assert questions_error_map == {
            str(singlechoice_question.uuid): "Ответ отсутствует",
            str(multichoice_question.uuid): "Ответ отсутствует",
            str(text_question.uuid): "Ответ отсутствует",
        }

    @pytest.mark.parametrize("username", ["test.user"])
    def test_correct_sql_query(self, client: APIClient, username: str) -> None:
        """Тест на корректную выборку ответов пользователя"""
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
        multichoice_answer_1 = AnswerOption.objects.create(
            question=question, seq_id=1, name="multichoice option 1"
        )
        multichoice_answer_2 = AnswerOption.objects.create(
            question=question, seq_id=2, name="multichoice option 2"
        )
        singlechoice_answer_1 = AnswerOption.objects.create(
            question=question_2, seq_id=1, name="singlechoice option 1"
        )
        singlechoice_answer_2 = AnswerOption.objects.create(
            question=question_2, seq_id=2, name="singlechoice option 2"
        )
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
            user_response=user_response,
            question=question,
            answer_option=multichoice_answer_1,
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
            answer_option=singlechoice_answer_2,
        )

        user_answer_request = UserAnswerRequest(survey=survey, user=user)
        questions_qs = Question.objects.get_survey_questions(
            survey=user_answer_request.survey, user=user_answer_request.user
        )

        fetched_questions = list(questions_qs)
        fetched_question_ids = {q.uuid for q in fetched_questions}
        expected_question_ids = {question.uuid, question_2.uuid, question_3.uuid}
        assert (
            fetched_question_ids == expected_question_ids,
            "Набор вопросов не соответствует ожидаемому",
        )

        for q in fetched_questions:
            assert q.answers.exists(), f"У вопроса {q.name} должны быть ответы"
            assert q.answer_options.exists(), f"У вопроса {q.name} должны быть варианты ответов"

            if q.type == Question.QuestionType.MULTIPLE_CHOICE:
                answer_count = 2
                assert (
                    q.answers.count() == answer_count,
                    "Должно быть 2 ответа для вопроса с множественным выбором",
                )
            if q.type == Question.QuestionType.SINGLE_CHOICE:
                answer_count = 1
                assert (
                    q.answers.count() == answer_count,
                    "Должен быть 1 ответ для вопроса с одиночным выбором",
                )

            if q.type == Question.QuestionType.TEXT:
                answer_count = 1
                assert (
                    q.answers.count() == answer_count,
                    "Должен быть 1 ответ для текстового вопроса",
                )

    @pytest.mark.parametrize("username", ["test.user"])
    def test_get_stat_survey(
        self, client: APIClient, username: str, generate_user_answers: Survey
    ) -> None:
        """Тест на корректность получения статистики по опросу"""
        survey = generate_user_answers
        response = client.get(self._get_stat_url(survey.uuid))

        survey_data = {
            "questions_len": 2,
            "total_count_question_0": 10,
            "total_count_question_1": 5,
            "answer_count_1": 5,
            "percentage_answer_1": 50.0,
            "answer_count_2": 5,
            "percentage_answer_2": 50.0,
        }

        assert response.status_code == status.HTTP_200_OK
        assert UserResponse.objects.all().count() == self.USER_RESPONSES_COUNT
        assert response.data["name"] == "Survey 1"
        assert len(response.data["questions"]) == survey_data["questions_len"]
        assert response.data["questions"][0]["name"] == "Вопрос 1"
        assert response.data["questions"][0]["type"] == "multiple_choice"
        assert response.data["questions"][0]["total_count"] == survey_data["total_count_question_0"]
        assert response.data["questions"][1]["name"] == "Вопрос 2"
        assert response.data["questions"][1]["type"] == "text"
        assert response.data["questions"][1]["total_count"] == survey_data["total_count_question_1"]
        assert response.data["questions"][0]["answers"][0]["name"] == "Option 1"
        assert response.data["questions"][0]["answers"][0]["count"] == survey_data["answer_count_1"]
        assert (
            response.data["questions"][0]["answers"][0]["percentage"],
            survey_data["percentage_answer_1"],
        )
        assert response.data["questions"][0]["answers"][1]["name"] == "Option 3"
        assert response.data["questions"][0]["answers"][1]["count"] == survey_data["answer_count_2"]
        assert (
            response.data["questions"][0]["answers"][1]["percentage"],
            survey_data["percentage_answer_2"],
        )

    @pytest.mark.parametrize("username", ["test.user"])
    def test_wrong_stat_url(self, client: APIClient, username: str) -> None:
        """Тест на ошибочный переход survey-stat"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        response = client.get(self._get_stat_url(survey.uuid))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "Не являетесь владельцем опроса"

    @pytest.mark.parametrize("username", ["test.user"])
    def test_empty_stat_url(self, client: APIClient, username: str) -> None:
        """Тест обработки ошибки при получении пустой статистики"""
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey.owner_user_ids.add(user)

        response = client.get(self._get_stat_url(survey.uuid))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "Статистика по опросу отсутствует"

    @pytest.mark.parametrize("username", ["test.user"])
    @pytest.mark.parametrize("anonymous, users_count", [(False, 5), (True, 4)])
    def test_get_user_stat_survey(
        self, client: APIClient, username: str, anonymous: bool, users_count: int
    ) -> None:
        """Тест на корректность получения статистики по опросу по сотрудникам"""

        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=anonymous
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey.owner_user_ids.add(user)
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        question_2 = Question.objects.create(
            survey=survey, seq_id=2, name="Вопрос 2", type=Question.QuestionType.TEXT
        )
        answer_option_1 = AnswerOption.objects.create(
            question=question, seq_id=1, name="Option 1", is_active=True
        )
        AnswerOption.objects.create(question=question, seq_id=2, name="Option 2", is_active=True)
        answer_option_3 = AnswerOption.objects.create(
            question=question, seq_id=3, name="Option 3", is_active=True
        )
        users = list(range(5))
        serial_number = 3
        for i in users:
            user = CustomUser.objects.create(
                username=f"test.user{i}", password="1234", email=f"test.user{i}@example.com"
            )
            if anonymous and i == serial_number:
                user = None
            user_response = UserResponse.objects.create(
                survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
            )
            UserAnswer.objects.create(
                user_response=user_response,
                question=question,
                answer_option=answer_option_1,
            )
            UserAnswer.objects.create(
                user_response=user_response,
                question=question,
                answer_option=answer_option_3,
            )
            UserAnswer.objects.create(
                user_response=user_response,
                question=question_2,
                text_answer=f"Тестовый текст от пользователя {i}",
            )
            user_response.status = UserResponse.Status.COMPLETED
            user_response.save()

        response = client.get(self._get_user_stat_url(survey.uuid))

        survey_data = {
            "survey_name": "Survey 1",
            "survey_status": "draft",
            "users": users_count,
            "questions": 2,
            "question_name_1": "Вопрос 1",
            "question_type_1": "multiple_choice",
            "answer_name_question_1": "Option 1",
            "answer_name_2_question_1": "Option 3",
            "question_name_2": "Вопрос 2",
            "question_type_2": "text",
            "answer_name_question_2": "Тестовый текст от пользователя 0",
        }

        assert response.status_code == status.HTTP_200_OK

        response_data = response.data["results"]
        assert response_data["name"] == survey_data["survey_name"]
        assert response_data["status"] == survey_data["survey_status"]
        assert len(response_data["users"]) == survey_data["users"]
        assert len(response_data["users"][0]["questions"]) == survey_data["questions"]
        assert response_data["users"][0]["questions"][0]["name"] == survey_data["question_name_1"]
        assert response_data["users"][0]["questions"][0]["type"] == survey_data["question_type_1"]
        assert (
            response_data["users"][0]["questions"][0]["answers"][0]["name"]
            == survey_data["answer_name_question_1"]
        )
        assert (
            response_data["users"][0]["questions"][0]["answers"][1]["name"]
            == survey_data["answer_name_2_question_1"]
        )
        assert response_data["users"][0]["questions"][1]["name"] == survey_data["question_name_2"]
        assert response_data["users"][0]["questions"][1]["type"] == survey_data["question_type_2"]
        assert (
            response_data["users"][0]["questions"][1]["answers"][0]["name"]
            == survey_data["answer_name_question_2"]
        )

    @pytest.mark.parametrize("username", ["test.user"])
    def test_get_user_stat_survey_only_anonymous_answers(
        self, client: APIClient, username: str
    ) -> None:
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=True
        )
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey.owner_user_ids.add(user)
        question = Question.objects.create(
            survey=survey, seq_id=1, name="Вопрос 1", type=Question.QuestionType.MULTIPLE_CHOICE
        )
        question_2 = Question.objects.create(
            survey=survey, seq_id=2, name="Вопрос 2", type=Question.QuestionType.TEXT
        )
        answer_option_1 = AnswerOption.objects.create(
            question=question, seq_id=1, name="Option 1", is_active=True
        )
        AnswerOption.objects.create(question=question, seq_id=2, name="Option 2", is_active=True)
        answer_option_3 = AnswerOption.objects.create(
            question=question, seq_id=3, name="Option 3", is_active=True
        )
        for i in range(5):
            user_response = UserResponse.objects.create(
                survey=survey, user=None, status=UserResponse.Status.IN_PROGRESS
            )
            UserAnswer.objects.create(
                user_response=user_response,
                question=question,
                answer_option=answer_option_1,
            )
            UserAnswer.objects.create(
                user_response=user_response,
                question=question,
                answer_option=answer_option_3,
            )
            UserAnswer.objects.create(
                user_response=user_response,
                question=question_2,
                text_answer=f"Тестовый текст от пользователя {i}",
            )
            user_response.status = UserResponse.Status.COMPLETED
            user_response.save()

        response = client.get(self._get_user_stat_url(survey.uuid))

        assert response.status_code == status.HTTP_200_OK
        assert not response.json()["count"]

    @pytest.mark.parametrize("username", ["test.user"])
    def test_wrong_user_stat_url(self, client: APIClient, username: str) -> None:
        """Тест на ошибочный переход survey-user-stat"""
        survey = Survey.objects.create(name="Survey 1", status=Survey.Status.DRAFT)

        response = client.get(self._get_user_stat_url(survey.uuid))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "Не являетесь владельцем опроса"

    @pytest.mark.parametrize("username", ["test.user"])
    def test_empty_user_stat_url(self, client: APIClient, username: str) -> None:
        """Тест обработки ошибки при получении пустой статистики по пользователям опроса"""
        survey = Survey.objects.create(name="Survey 1", status=Survey.Status.DRAFT)
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey.owner_user_ids.add(user)

        response = client.get(self._get_user_stat_url(survey.uuid))

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data["error"] == "Статистика по опросу отсутствует"

    @pytest.mark.parametrize("username", ["test.user"])
    @pytest.mark.parametrize(
        "new_status, status_code, error_msg",
        [
            (
                Survey.Status.ACTIVE,
                status.HTTP_400_BAD_REQUEST,
                "В опросе должен быть хотя бы один вопрос",
            ),
            (Survey.Status.DRAFT, status.HTTP_200_OK, None),
            (Survey.Status.CLOSED, status.HTTP_200_OK, None),
        ],
    )
    def test_survey_without_questions(
        self,
        client: APIClient,
        username: str,
        new_status: Survey.Status,
        status_code: int,
        error_msg: str,
    ) -> None:
        """Тест на проверку опроса без вопросов"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Опрос без вопросов", status=Survey.Status.DRAFT, is_anonymous=False
        )
        survey.owner_user_ids.add(user)

        response = client.patch(self._get_detail_url(survey.uuid), data={"status": new_status})

        assert response.status_code == status_code
        assert response.data.get("error") == error_msg

    @pytest.mark.parametrize("username", ["test.user"])
    @pytest.mark.parametrize(
        "new_status, status_code, error_msg",
        [
            (
                Survey.Status.ACTIVE,
                status.HTTP_400_BAD_REQUEST,
                "В опросе присутствует вопрос с пустым названием",
            ),
        ],
    )
    def test_survey_without_questions_name(
        self,
        client: APIClient,
        username: str,
        new_status: Survey.Status,
        status_code: int,
        error_msg: str,
    ) -> None:
        """Тест на проверку опроса с пустым названием вопроса"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Опрос без вопросов", status=Survey.Status.DRAFT, is_anonymous=False
        )
        survey.owner_user_ids.add(user)

        Question.objects.create(
            survey=survey,
            seq_id=1,
            name="",
            type=Question.QuestionType.SINGLE_CHOICE,
        )

        response = client.patch(self._get_detail_url(survey.uuid), data={"status": new_status})

        assert response.status_code == status_code
        assert response.data.get("error") == error_msg

    @pytest.mark.parametrize("username", ["test.user"])
    def test_survey_without_name(
        self,
        client: APIClient,
        username: str,
    ) -> None:
        """Тест на проверку опроса с пустым названием"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(name="", status=Survey.Status.DRAFT, is_anonymous=False)
        survey.owner_user_ids.add(user)

        Question.objects.create(
            survey=survey,
            seq_id=1,
            name="Вопрос 1",
            type=Question.QuestionType.SINGLE_CHOICE,
        )

        response = client.patch(
            self._get_detail_url(survey.uuid), data={"status": Survey.Status.ACTIVE}
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.data.get("error") == "У опроса отсутствует название"

    @pytest.mark.parametrize("username", ["test.user"])
    @pytest.mark.parametrize(
        "new_status, status_code, error_msg",
        [
            (
                Survey.Status.ACTIVE,
                status.HTTP_400_BAD_REQUEST,
                "Для вопроса '{question_name}' должен быть хотя бы один вариант ответа",
            ),
            (Survey.Status.DRAFT, status.HTTP_200_OK, ""),
            (Survey.Status.CLOSED, status.HTTP_200_OK, ""),
        ],
    )
    def test_question_without_answers(
        self,
        client: APIClient,
        username: str,
        new_status: Survey.Status,
        status_code: int,
        error_msg: str,
    ) -> None:
        """Тест на проверку вопроса без вариантов ответа"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Опрос без вариантов ответа", status=Survey.Status.DRAFT, is_anonymous=False
        )
        survey.owner_user_ids.add(user)

        question = Question.objects.create(
            survey=survey,
            seq_id=1,
            name="Вопрос без ответов",
            type=Question.QuestionType.SINGLE_CHOICE,
        )

        response = client.patch(self._get_detail_url(survey.uuid), data={"status": new_status})

        assert response.status_code == status_code
        assert response.data.get("error", "") == error_msg.format(question_name=question.name)

    @pytest.mark.parametrize("username", ["test.user"])
    @pytest.mark.parametrize(
        "new_status, status_code, error_msg",
        [
            (
                Survey.Status.ACTIVE,
                status.HTTP_400_BAD_REQUEST,
                "Для вопроса '{question_name}' должны быть заполнены все ответы",
            ),
        ],
    )
    def test_question_without_answers_name(
        self,
        client: APIClient,
        username: str,
        new_status: Survey.Status,
        status_code: int,
        error_msg: str,
    ) -> None:
        """Тест на проверку вопроса со всеми заполненными названиями ответов"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Опрос без вариантов ответа", status=Survey.Status.DRAFT, is_anonymous=False
        )
        survey.owner_user_ids.add(user)

        question = Question.objects.create(
            survey=survey,
            seq_id=1,
            name="Вопрос без ответов",
            type=Question.QuestionType.SINGLE_CHOICE,
        )
        AnswerOption.objects.create(question=question, seq_id=1, name="", is_active=True)

        response = client.patch(self._get_detail_url(survey.uuid), data={"status": new_status})

        assert response.status_code == status_code
        assert response.data.get("error", "") == error_msg.format(question_name=question.name)

    @pytest.mark.parametrize("username, new_status", [("test.user", Survey.Status.ACTIVE)])
    def test_create_survey_validator(
        self, client: APIClient, username: str, new_status: Survey.Status
    ) -> None:
        """Тест на проверку валидатором создания опроса c 3 типами вопросов"""
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Опрос для проверки валидации", status=Survey.Status.DRAFT
        )

        survey.owner_user_ids.add(user)

        question_single_choice = Question.objects.create(
            survey=survey,
            seq_id=1,
            name="Вопрос single_choice",
            type=Question.QuestionType.SINGLE_CHOICE,
        )
        question_multi_choice = Question.objects.create(
            survey=survey,
            seq_id=2,
            name="Вопрос multi_choice",
            type=Question.QuestionType.MULTIPLE_CHOICE,
        )
        Question.objects.create(
            survey=survey, seq_id=3, name="Вопрос text", type=Question.QuestionType.TEXT
        )
        AnswerOption.objects.create(
            question=question_single_choice, seq_id=1, name="single choice option 1", is_active=True
        )
        AnswerOption.objects.create(
            question=question_single_choice, seq_id=2, name="single choice option 2", is_active=True
        )
        AnswerOption.objects.create(
            question=question_multi_choice, seq_id=1, name="multi choice 1", is_active=True
        )
        AnswerOption.objects.create(
            question=question_multi_choice, seq_id=2, name="multi choice 2", is_active=True
        )
        AnswerOption.objects.create(
            question=question_multi_choice, seq_id=3, name="multi choice 3", is_active=True
        )

        response = client.patch(self._get_detail_url(survey.uuid), data={"status": new_status})

        assert response.status_code == status.HTTP_200_OK

    @pytest.mark.parametrize("username", ["test.user"])
    def test_success_can_finish(self, client: APIClient, username: str) -> None:
        """
        Тест на положительную возможность завершать опрос.
        Когда даны ответы на все вопросы опроса
        """
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Опрос для проверки валидации", status=Survey.Status.DRAFT
        )

        survey.owner_user_ids.add(user)

        user_response = UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )

        question_single_choice = Question.objects.create(
            survey=survey,
            seq_id=1,
            name="Вопрос single_choice",
            type=Question.QuestionType.SINGLE_CHOICE,
        )
        question_multi_choice = Question.objects.create(
            survey=survey,
            seq_id=2,
            name="Вопрос multi_choice",
            type=Question.QuestionType.MULTIPLE_CHOICE,
        )
        answer_single_choice = AnswerOption.objects.create(
            question=question_single_choice, seq_id=1, name="single choice option 1", is_active=True
        )
        answer_multi_choice = AnswerOption.objects.create(
            question=question_multi_choice, seq_id=1, name="multi choice 1", is_active=True
        )

        UserAnswer.objects.create(
            user_response=user_response,
            question=question_single_choice,
            answer_option=answer_single_choice,
        )

        UserAnswer.objects.create(
            user_response=user_response,
            question=question_multi_choice,
            answer_option=answer_multi_choice,
        )

        response = client.get(self._get_detail_url(survey.uuid))

        assert response.status_code == status.HTTP_200_OK
        assert response.data["can_finish"]

    @pytest.mark.parametrize("username", ["test.user"])
    def test_failed_can_finish(self, client: APIClient, username: str) -> None:
        """
        Тест на отрицательную возможность завершать опрос.
        Когда не на все вопросы опроса даны ответы
        """
        user, _ = CustomUser.objects.get_or_create(
            username=username, defaults={"email": "test@example.com"}
        )
        survey = Survey.objects.create(
            name="Опрос для проверки валидации", status=Survey.Status.DRAFT
        )

        survey.owner_user_ids.add(user)

        user_response = UserResponse.objects.create(
            survey=survey, user=user, status=UserResponse.Status.IN_PROGRESS
        )

        question_single_choice = Question.objects.create(
            survey=survey,
            seq_id=1,
            name="Вопрос single_choice",
            type=Question.QuestionType.SINGLE_CHOICE,
        )

        Question.objects.create(
            survey=survey,
            seq_id=2,
            name="Вопрос multi_choice",
            type=Question.QuestionType.MULTIPLE_CHOICE,
        )

        answer_single_choice = AnswerOption.objects.create(
            question=question_single_choice, seq_id=1, name="single choice option 1", is_active=True
        )

        UserAnswer.objects.create(
            user_response=user_response,
            question=question_single_choice,
            answer_option=answer_single_choice,
        )

        response = client.get(self._get_detail_url(survey.uuid))

        assert response.status_code == status.HTTP_200_OK
        assert not response.data["can_finish"]
