from datetime import datetime, timedelta
from typing import Callable

import pytest
from django.utils import timezone
from profiles.models import CustomUser
from survey.models import Survey


@pytest.mark.django_db
class TestSurveyModel:
    @pytest.fixture
    def survey(self) -> Survey:
        user1 = CustomUser.objects.create(
            username="test.user", password="1234", email="test.user@example.com"
        )
        CustomUser.objects.create(
            username="test.user1", password="1234", email="test.user1@example.com"
        )
        survey = Survey.objects.create(
            name="Survey 1", status=Survey.Status.DRAFT, is_anonymous=False
        )
        survey.owner_user_ids.add(user1)
        return survey

    @pytest.mark.parametrize(("username", "result"), [("test.user", True), ("test.user1", False)])
    def test_is_user_owner(self, survey: Survey, username: str, result: bool) -> None:
        user = CustomUser.objects.get(username=username)

        assert survey.is_user_owner(user) is result

    @pytest.mark.parametrize(
        "status, datetime_check_func",
        [
            (Survey.Status.ACTIVE, lambda x: x is None),  # type: ignore
            (Survey.Status.DRAFT, lambda x: x is None),  # type: ignore
            (
                Survey.Status.CLOSED,
                lambda x: timezone.now() > x < timezone.now() + timedelta(minutes=1),  # type: ignore
            ),
            (Survey.Status.ARCHIVED, lambda x: x is None),  # type: ignore
        ],
    )
    def test_save(
        self, survey: Survey, status: str, datetime_check_func: Callable[[datetime | None], bool]
    ) -> None:
        survey.status = status
        survey.save()
        survey.refresh_from_db()

        assert datetime_check_func(survey.end_date)
