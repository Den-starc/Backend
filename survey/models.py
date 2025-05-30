import uuid
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db import models
from django.db.models import (
    Count,
    ExpressionWrapper,
    F,
    FloatField,
    OuterRef,
    Prefetch,
    Q,
    QuerySet,
    Subquery,
)
from django.db.models.functions import Round
from django.utils import timezone
from profiles.models import CustomUser

from hyperus_backend.models.base.timestamp_mixin import TimeStampedBaseModel


class SurveyManager(models.Manager):
    def get_user_response(
        self,
        survey: "Survey",
        user: AbstractBaseUser | AnonymousUser | None,
        user_response_uuid: str | None,
    ) -> "UserResponse":
        if survey.is_anonymous:
            return survey.user_responses.filter(uuid=user_response_uuid).first()
        return survey.user_responses.filter(user=user).first()

    def filter_by_action(self, filter_action: str | None, user_id: int) -> QuerySet["Survey"]:
        if filter_action == "all_active":
            return self._all_active()
        return self._own_surveys(user_id)

    def _all_active(self) -> QuerySet["Survey"]:
        """Возвращает все активные опросы"""
        return self.get_queryset().filter(status=Survey.Status.ACTIVE)

    def _own_surveys(self, user_id: int) -> QuerySet["Survey"]:
        """Возвращает опросы пользователя, исключая архивные"""
        return (
            self.get_queryset()
            .filter(owner_user_ids=user_id)
            .exclude(status=Survey.Status.ARCHIVED)
        )


class Survey(TimeStampedBaseModel):
    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"
        ARCHIVED = "archived", "Archived"

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.DRAFT)
    owner_user_ids = models.ManyToManyField(CustomUser, related_name="owner_surveys")
    end_date = models.DateTimeField(null=True, blank=True)
    is_anonymous = models.BooleanField(default=False)

    objects = SurveyManager()

    def is_user_owner(self, user: AbstractBaseUser | AnonymousUser) -> bool:
        return user.pk in self.owner_user_ids.values_list("pk", flat=True)

    def save(self, *args: Any, **kwargs: Any) -> None:
        super().save(*args, **kwargs)
        if self.status == self.Status.CLOSED:
            self.end_date = timezone.now()
            super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class QuestionManager(models.Manager):
    def get_survey_questions(
        self, survey: Survey, user: AbstractBaseUser | None, user_response: str | None = None
    ) -> QuerySet:
        return (
            self.filter(
                Q(survey__user_responses__user=user)
                if user
                else Q(survey__user_responses__uuid=user_response),
                survey=survey,
            )
            .select_related("survey")
            .prefetch_related(
                Prefetch(
                    "answers",
                    queryset=UserAnswer.objects.filter(
                        Q(user_response__user=user)
                        if user
                        else Q(user_response__uuid=user_response)
                    ),
                ),
                Prefetch(
                    "answer_options",
                    queryset=AnswerOption.objects.filter(question__survey=survey),
                ),
            )
        )


class Question(TimeStampedBaseModel):
    class QuestionType(models.TextChoices):
        SINGLE_CHOICE = "single_choice", "Single Choice"
        MULTIPLE_CHOICE = "multiple_choice", "Multiple Choice"
        TEXT = "text", "Text"

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="questions")
    seq_id = models.SmallIntegerField()
    name = models.CharField(max_length=10000, blank=True)
    type = models.CharField(max_length=20, choices=QuestionType.choices)
    is_active = models.BooleanField(default=True)

    objects = QuestionManager()

    class Meta:
        unique_together = ("survey", "seq_id")
        indexes = [models.Index(fields=["survey", "seq_id"])]
        ordering = ["seq_id"]

    def __str__(self) -> str:
        return f"{self.survey.name} - {self.name}"


class AnswerOption(TimeStampedBaseModel):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question = models.ForeignKey(
        Question, on_delete=models.CASCADE, related_name="answer_options", to_field="uuid"
    )
    seq_id = models.IntegerField()
    name = models.CharField(max_length=512, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        unique_together = ("question", "seq_id")
        indexes = [models.Index(fields=["question", "seq_id"])]
        ordering = ["seq_id"]

    def __str__(self) -> str:
        return self.name


class UserResponse(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "IN_PROGRESS", "В процессе"
        COMPLETED = "COMPLETED", "Завершен"

    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name="user_responses")
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)

    class Meta:
        indexes = [models.Index(fields=["survey", "user"])]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.uuid}"


class UserAnswerManager(models.Manager):
    def get_survey_stat(self, survey_uuid: str) -> QuerySet:
        total_counts = (
            UserAnswer.objects.filter(
                question_id=OuterRef("question_id"),
                user_response__survey__uuid=survey_uuid,
                user_response__status=UserResponse.Status.COMPLETED,
            )
            .values("question_id")
            .annotate(total_count=Count("uuid"))
            .values("total_count")
        )

        return (
            UserAnswer.objects.filter(
                user_response__survey__uuid=survey_uuid,
                user_response__status=UserResponse.Status.COMPLETED,
            )
            .values(
                "question_id__survey_id__uuid",
                "question_id__survey_id__name",
                "question_id__survey_id__status",
                "question_id__uuid",
                "question_id__name",
                "question_id__type",
                "answer_option_id__uuid",
                "answer_option_id__name",
            )
            .annotate(answer_count=Count("uuid"))
            .annotate(total_count=Subquery(total_counts))
            .annotate(
                percentage=Round(
                    ExpressionWrapper(
                        F("answer_count") * 100.0 / F("total_count"), output_field=FloatField()
                    ),
                    2,
                )
            )
            .order_by("question_id__seq_id", "answer_option__seq_id")
        )

    def get_survey_user_stat(self, survey_uuid: str) -> QuerySet:
        return (
            (
                UserAnswer.objects.select_related(
                    "user_response",
                    "user_response__user",
                    "user_response__survey",
                    "question",
                    "answer_option",
                ).filter(
                    user_response__survey__uuid=survey_uuid,
                    user_response__status=UserResponse.Status.COMPLETED,
                    user_response__user__isnull=False,
                )
            )
            .annotate(
                survey_uuid=F("user_response__survey__uuid"),
                survey_name=F("user_response__survey__name"),
                survey_status=F("user_response__survey__status"),
                user_id=F("user_response__user__id"),
                first_name=F("user_response__user__first_name"),
                last_name=F("user_response__user__last_name"),
                completed_at=F("user_response__completed_at"),
                question_uuid=F("question__uuid"),
                question_name=F("question__name"),
                question_type=F("question__type"),
                answer_uuid=F("answer_option__uuid"),
                answer_name=F("answer_option__name"),
            )
            .order_by("user_response__user_id")
        )


class UserAnswer(models.Model):
    uuid = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user_response = models.ForeignKey(
        UserResponse, on_delete=models.CASCADE, related_name="user_answers"
    )
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    answer_option = models.ForeignKey(AnswerOption, on_delete=models.CASCADE, null=True, blank=True)
    text_answer = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = UserAnswerManager()

    class Meta:
        ordering = ["question__seq_id"]

    def __str__(self) -> str:
        return f"{self.uuid}"
