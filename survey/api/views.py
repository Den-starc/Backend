from http import HTTPMethod
from typing import Any

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.db import transaction
from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404
from django_filters import rest_framework as filters
from drf_spectacular.utils import extend_schema
from rest_framework import filters as rest_filters
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.request import Request
from rest_framework.response import Response

from hyperus_backend.pagination import StandardResultsSetPagination, SurveyResultsSetPagination
from survey.api.serializers import (
    AnswerOptionSerializer,
    CreateOrUpdateUserAnswerSerializer,
    ErrorCompleteResponseSerializer,
    ErrorResponseSerializer,
    PaginatedSurveyUserStatResponseSerializer,
    QuestionSerializer,
    SurveySerializer,
    SurveyStatSerializer,
    UserAnswerRequestSerializer,
    UserAnswerSerializer,
)
from survey.domain.entities import SurveyFilterAction, UserAnswerRequest, UserAnswerUpdate
from survey.exceptions import ValidateAnswerError
from survey.models import AnswerOption, Question, Survey, UserAnswer, UserResponse
from survey.services.answer_update import QuestionHandlerFactory
from survey.services.complete_survey import CompleteSurveyService, StatSurveyService
from survey.services.manage_permissions import PermissionManager
from survey.services.validators import (
    CompletedSurveyChecker,
    CreateSurveyChecker,
    SurveyStatChecker,
    UserAnswerChecker,
    UserResponseChecker,
)


class SurveyFilter(filters.FilterSet):
    name = filters.CharFilter(lookup_expr="icontains")
    status = filters.ChoiceFilter(choices=Survey.Status.choices)
    created_at = filters.DateTimeFromToRangeFilter()
    is_anonymous = filters.BooleanFilter()
    filter_action = filters.ChoiceFilter(
        choices=[(item.value, item.name) for item in SurveyFilterAction],
        method=lambda queryset, name, value: queryset,  # type: ignore
    )  # Данное поле исключительно для OpenAPI

    class Meta:
        model = Survey
        fields = ["name", "status", "created_at", "is_anonymous"]


class SurveyViewSet(
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.ListModelMixin,
    viewsets.GenericViewSet,
):
    """
    API endpoint для управления опросами (surveys).
    Предоставляет CRUD операции для работы с опросами
    """

    queryset: QuerySet[Survey] = Survey.objects.all()
    serializer_class = SurveySerializer
    permission_classes = [IsAuthenticated]
    lookup_field = "uuid"
    filter_backends = [filters.DjangoFilterBackend, rest_filters.OrderingFilter]
    filterset_class = SurveyFilter
    ordering_fields = ["name", "status", "end_date", "is_anonymous"]
    ordering = ["name"]
    pagination_class = StandardResultsSetPagination

    def get_permissions(self) -> list[BasePermission]:
        return PermissionManager.check(
            self.request.user, self.action, self.queryset, self.kwargs.get("uuid")
        )

    def update(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        survey = self.get_object()
        checker = CreateSurveyChecker()
        user_answer_request = UserAnswerRequest(user=request.user, survey=survey)
        if request.data.get("status") == Survey.Status.ACTIVE:
            try:
                checker.validate(user_answer_request=user_answer_request)
            except ValidateAnswerError as error:
                return Response({"error": error.message}, status=status.HTTP_400_BAD_REQUEST)
        return super().update(request, *args, **kwargs)

    def list(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        filter_action = request.query_params.get("filter_action")
        user_id = request.user.id
        queryset = self.filter_queryset(Survey.objects.filter_by_action(filter_action, user_id))
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    def get_serializer_context(self) -> dict[str, Any]:
        """Автоматическое прокидывние объекта пользователя в сериализатор"""
        context = super().get_serializer_context()
        context.update(user=self.request.user)
        return context

    @extend_schema(
        responses={
            status.HTTP_200_OK: SurveySerializer,
            status.HTTP_400_BAD_REQUEST: ErrorResponseSerializer,
        },
    )
    def retrieve(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        survey_uuid = kwargs["uuid"]
        survey = get_object_or_404(self.get_queryset(), uuid=survey_uuid)
        user = None if survey.is_anonymous else request.user
        serializer = self.get_serializer(
            instance=survey,
            context={
                "user": request.user,
                "user_response_uuid": request.COOKIES.get("user_response_uuid"),
            },
        )
        checker = UserResponseChecker()
        user_answer_request = UserAnswerRequest(user=user, survey=survey)
        try:
            checker.validate(user_answer_request=user_answer_request)
        except ValidateAnswerError as error:
            return Response({"error": error.message}, status=status.HTTP_400_BAD_REQUEST)
        return Response(serializer.data)

    @extend_schema(
        responses={
            status.HTTP_200_OK: SurveySerializer,
            status.HTTP_400_BAD_REQUEST: ErrorCompleteResponseSerializer,
        },
    )
    @action(detail=True, methods=[HTTPMethod.POST])
    def complete(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """
        Обработка завершения опроса при успешной валидации
        """
        survey_uuid = kwargs["uuid"]
        user_response_uuid = request.COOKIES.get("user_response_uuid")

        survey = get_object_or_404(self.get_queryset(), uuid=survey_uuid)
        user = None if survey.is_anonymous else request.user
        checker = CompletedSurveyChecker()
        user_answer_request = UserAnswerRequest(
            user=user, survey=survey, user_response_uuid=user_response_uuid
        )

        try:
            checker.validate(user_answer_request=user_answer_request)
        except ValidateAnswerError as error:
            return Response(
                {"error": error.message, "questions_error_map": error.question_error_map},
                status=status.HTTP_400_BAD_REQUEST,
            )
        CompleteSurveyService.complete_survey(survey, user, user_response_uuid)
        serializer = self.get_serializer(instance=survey)
        return Response(serializer.data, status=status.HTTP_200_OK)

    @extend_schema(
        responses={
            status.HTTP_200_OK: SurveyStatSerializer,
            status.HTTP_400_BAD_REQUEST: ErrorResponseSerializer,
        },
    )
    @action(detail=True, methods=[HTTPMethod.GET])
    def stat(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """
        Получение общей статистики прохождения пользователями опроса
        """
        survey_uuid = kwargs["uuid"]
        survey = get_object_or_404(self.get_queryset(), uuid=survey_uuid)
        checker = SurveyStatChecker()
        user_answer_request = UserAnswerRequest(user=request.user, survey=survey)
        try:
            checker.validate(user_answer_request=user_answer_request)
        except ValidateAnswerError as error:
            return Response({"error": error.message}, status=status.HTTP_400_BAD_REQUEST)

        answer_counts = UserAnswer.objects.get_survey_stat(survey_uuid)
        survey_stat = StatSurveyService.stat_survey(answer_counts)
        survey_stat = StatSurveyService.add_null_answer_options(survey_stat)
        return Response(survey_stat)

    @extend_schema(
        responses={
            status.HTTP_200_OK: PaginatedSurveyUserStatResponseSerializer,
            status.HTTP_400_BAD_REQUEST: ErrorResponseSerializer,
        },
    )
    @action(
        detail=True,
        methods=[HTTPMethod.GET],
        pagination_class=SurveyResultsSetPagination,
        url_path="stat-user",
    )
    def stat_user(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """
        Получение общей статистики прохождения опроса по пользователям
        """
        survey_uuid = kwargs["uuid"]
        survey = self.get_queryset().get(uuid=survey_uuid)
        checker = SurveyStatChecker()
        user_answer_request = UserAnswerRequest(user=request.user, survey=survey)
        try:
            checker.validate(user_answer_request=user_answer_request)
        except ValidateAnswerError as error:
            return Response({"error": error.message}, status=status.HTTP_400_BAD_REQUEST)

        answer_counts = UserAnswer.objects.get_survey_user_stat(survey_uuid)
        survey_stat = StatSurveyService.stat_user_survey(answer_counts, request)

        users: list[str] = list(survey_stat["users"])
        users_page = self.paginate_queryset(users)

        stats_without_users = {k: v for k, v in survey_stat.items() if k != "users"}
        return self.get_paginated_response({"users": users_page, **stats_without_users})


class QuestionViewSet(
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    API endpoint для управления вопросами (questions).
    Предоставляет CRUD операции для работы с вопросами
    """

    queryset = Question.objects.all()
    serializer_class = QuestionSerializer
    permission_classes = [IsAuthenticated]

    @transaction.atomic
    def perform_update(self, serializer: QuestionSerializer) -> None:  # type: ignore
        """
        Данный метод нужен для того, чтобы удалять варианты ответов
        при смене типа вопроса на текстовый.
        """
        if serializer.validated_data.get("type") == Question.QuestionType.TEXT:
            serializer.instance.answer_options.all().delete()  # type: ignore
        super().perform_update(serializer)


class AnswerOptionViewSet(
    mixins.CreateModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """
    API endpoint для управления вариантами ответов (answer options).
    Предоставляет CRUD операции для работы с вариантами ответов
    """

    queryset = AnswerOption.objects.all()
    serializer_class = AnswerOptionSerializer
    permission_classes = [IsAuthenticated]


class UserAnswerViewSet(
    mixins.CreateModelMixin,
    viewsets.GenericViewSet,
):
    """
    API endpoint для управления конкретными ответами на вопросы.
    Предоставляет CRUD операции для работы с ответами на вопросы
    """

    queryset = UserAnswer.objects.all()
    serializer_class = UserAnswerSerializer
    permission_classes = [IsAuthenticated]
    COOKIE_TTL = 60 * 60 * 24 * 7  # 7 дней

    def get_permissions(self) -> list[BasePermission]:
        return PermissionManager.check(
            self.request.user, self.action, Survey.objects.all(), self.request.data.get("survey")
        )

    @extend_schema(
        responses={
            status.HTTP_200_OK: CreateOrUpdateUserAnswerSerializer,
            status.HTTP_400_BAD_REQUEST: ErrorResponseSerializer,
        },
    )
    def create(self, request: Request, *args: Any, **kwargs: Any) -> Response:
        """
        Создание или обновление ответа пользователя на основе типа вопроса
        """
        serializer = self._get_validated_serializer(request)
        data = serializer.validated_data

        survey = get_object_or_404(Survey, uuid=data["survey"])
        question = get_object_or_404(Question, uuid=data["question"])
        answer_option = self._get_answer_option(data.get("answer_option"))
        user = None if survey.is_anonymous else request.user
        user_response_uuid = request.COOKIES.get("user_response_uuid")

        try:
            self._validate_answer(data, user, survey, user_response_uuid)
        except ValidateAnswerError as error:
            return Response({"error": error.message}, status=status.HTTP_400_BAD_REQUEST)

        user_response, created = self._get_or_create_user_response(user, survey, user_response_uuid)

        self._handle_answer(question, answer_option, data.get("text_answer"), user_response)

        return self._build_response(user, created, user_response)

    def _get_validated_serializer(self, request: Request) -> UserAnswerRequestSerializer:
        serializer = UserAnswerRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return serializer

    def _get_answer_option(self, uuid: str | None) -> AnswerOption | None:
        if not uuid:
            return None
        return get_object_or_404(AnswerOption, uuid=uuid)

    def _validate_answer(
        self,
        data: dict,
        user: AbstractBaseUser | AnonymousUser | None,
        survey: Survey,
        user_response_uuid: str | None,
    ) -> None:
        checker = UserAnswerChecker()
        user_answer_request = UserAnswerRequest(
            user=user,
            survey=survey,
            question=data["question"],
            answer_option=data.get("answer_option"),
            text_answer=data.get("text_answer"),
            user_response_uuid=user_response_uuid,
        )  # type: ignore
        checker.validate(user_answer_request)

    def _get_or_create_user_response(
        self,
        user: AbstractBaseUser | AnonymousUser | None,
        survey: Survey,
        user_response_uuid: str | None,
    ) -> tuple[UserResponse, bool]:
        filters = Q(uuid=user_response_uuid) if not user else Q(user=user)
        return UserResponse.objects.filter(filters).get_or_create(
            user=user,
            survey=survey,
            defaults={"status": UserResponse.Status.IN_PROGRESS},
        )

    def _handle_answer(
        self,
        question: Question,
        answer_option: AnswerOption | None,
        text_answer: str | None,
        user_response: UserResponse,
    ) -> None:
        handler = QuestionHandlerFactory.get_strategy(question.type)
        user_answer_update = UserAnswerUpdate(
            user_response=user_response,
            question=question,
            answer_option=answer_option,
            text_answer=text_answer,
        )
        handler.handle_answer(user_answer_update)

    def _build_response(
        self,
        user: AbstractBaseUser | AnonymousUser | None,
        created: bool,
        user_response: UserResponse,
    ) -> Response:
        response = Response(status=status.HTTP_204_NO_CONTENT)
        if not user and created:
            response.set_cookie(
                key="user_response_uuid",
                value=str(user_response.uuid),
                samesite="Lax",
                max_age=self.COOKIE_TTL,
            )
        return response
