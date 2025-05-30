from enum import StrEnum
from typing import Any

from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers
from rest_framework.utils.serializer_helpers import ReturnDict

from hyperus_backend.custom_validators_mixin import FieldLengthValidatorMixin
from survey.models import AnswerOption, Question, Survey, UserAnswer, UserResponse
from survey.services.checkers import CanFinishChecker, IsCompletedChecker


class SurveyActions(StrEnum):
    active = "Запустить опрос"
    close = "Закрыть опрос"
    delete = "Удалить опрос"
    get_stat = "Посмотреть статистику"


class ErrorResponseSerializer(serializers.Serializer):
    error = serializers.CharField()


class UserAnswerRequestSerializer(serializers.Serializer):
    survey = serializers.CharField(required=True)
    question = serializers.CharField(required=True)
    answer_option = serializers.CharField(required=False, allow_null=True, allow_blank=True)
    text_answer = serializers.CharField(required=False, allow_null=True, allow_blank=True)


class ErrorCompleteResponseSerializer(ErrorResponseSerializer):
    questions_error_map = serializers.DictField(child=serializers.CharField())


class AnswerOptionSerializer(FieldLengthValidatorMixin, serializers.ModelSerializer):
    class Meta:
        model = AnswerOption
        fields = ["uuid", "question", "seq_id", "name", "is_active"]
        max_field_lengths = {"name": 255}


class QuestionSerializer(FieldLengthValidatorMixin, serializers.ModelSerializer):
    answer_options = AnswerOptionSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = ["uuid", "survey", "answer_options", "seq_id", "name", "type", "is_active"]
        max_field_lengths = {"name": 10000}


class CreateOrUpdateUserAnswerSerializer(serializers.Serializer):
    can_finish = serializers.BooleanField(default=False)


class UserAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserAnswer
        fields = ["uuid", "user_response", "question", "answer_option", "text_answer", "created_at"]
        read_only_fields = ["uuid", "created_at"]


class UserResponseSerializer(serializers.ModelSerializer):
    answers = UserAnswerSerializer(many=True, read_only=True)

    class Meta:
        model = UserResponse
        fields = ["uuid", "survey", "user", "created_at", "completed_at", "status", "answers"]
        read_only_fields = ["uuid", "created_at"]


class SurveyActionsSerializer(serializers.Serializer):
    name: serializers.ChoiceField = serializers.ChoiceField(
        choices=[action.name for action in SurveyActions], required=True
    )
    label: serializers.CharField = serializers.CharField(required=True)


class SurveySerializer(FieldLengthValidatorMixin, serializers.ModelSerializer):
    questions = QuestionSerializer(many=True, read_only=True)
    user_answers = serializers.SerializerMethodField()
    status = serializers.ChoiceField(required=True, choices=Survey.Status.choices)
    is_user_owner = serializers.SerializerMethodField()
    can_finish = serializers.SerializerMethodField()
    is_completed = serializers.SerializerMethodField()
    actions = serializers.SerializerMethodField()

    class Meta:
        model = Survey
        fields = [
            "uuid",
            "name",
            "status",
            "owner_user_ids",
            "end_date",
            "is_anonymous",
            "questions",
            "user_answers",
            "is_user_owner",
            "can_finish",
            "is_completed",
            "actions",
        ]
        max_field_lengths = {"name": 255}

    @extend_schema_field(UserAnswerSerializer(many=True))
    def get_user_answers(self, obj: Survey) -> ReturnDict | list[Any]:
        user = self.context.get("user")
        user_response_uuid = self.context.get("user_response_uuid")
        if user:
            user_response = Survey.objects.get_user_response(obj, user, user_response_uuid)
            if user_response:
                answers = UserAnswer.objects.filter(user_response=user_response)
                return UserAnswerSerializer(answers, many=True).data
        return []

    def get_is_user_owner(self, obj: Survey) -> bool:
        user = self.context.get("user")
        if user:
            return obj.is_user_owner(user)
        raise serializers.ValidationError("Нет объекта пользователя в контексте сериализатра")

    def get_can_finish(self, obj: Survey) -> bool:
        user = self.context.get("user")
        user_response_uuid = self.context.get("user_response_uuid")
        can_finish = False
        if user:
            user_response = Survey.objects.get_user_response(obj, user, user_response_uuid)
            can_finish = CanFinishChecker().check(user_response)
        return can_finish

    def get_is_completed(self, obj: Survey) -> bool:
        user = self.context.get("user")
        user_response_uuid = self.context.get("user_response_uuid")
        is_completed = False
        if user:
            user_response = Survey.objects.get_user_response(obj, user, user_response_uuid)
            is_completed = IsCompletedChecker().check(user_response)
        return is_completed

    @extend_schema_field(SurveyActionsSerializer(many=True))
    def get_actions(self, obj: Survey) -> list[dict]:
        status_actions_map: dict[str, list[SurveyActions]] = {
            Survey.Status.DRAFT: [
                SurveyActions.active,
                SurveyActions.delete,
            ],
            Survey.Status.ACTIVE: [
                SurveyActions.close,
                SurveyActions.get_stat,
                SurveyActions.delete,
            ],
            Survey.Status.CLOSED: [
                SurveyActions.delete,
                SurveyActions.get_stat,
            ],
            Survey.Status.ARCHIVED: [
                SurveyActions.get_stat,
            ],
        }
        return [
            {"name": action.name, "label": action.value}
            for action in status_actions_map.get(obj.status, [])
        ]


class AnswerStatSerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    name = serializers.CharField()
    count = serializers.IntegerField()
    percentage = serializers.FloatField()


class QuestionStatSerializer(serializers.Serializer):
    uuid = serializers.CharField()
    name = serializers.CharField()
    type = serializers.CharField()
    total_count = serializers.IntegerField()
    answers = AnswerStatSerializer(many=True)


class SurveyStatSerializer(serializers.Serializer):
    uuid = serializers.CharField()
    name = serializers.CharField()
    status = serializers.CharField()
    questions = QuestionStatSerializer(many=True)


class AnswerUserStatSerializer(serializers.Serializer):
    uuid = serializers.UUIDField(allow_null=True)
    name = serializers.CharField()


class QuestionUserStatSerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    name = serializers.CharField()
    type = serializers.CharField()
    answers = AnswerUserStatSerializer(many=True)


class UserStatSerializer(serializers.Serializer):
    uuid = serializers.IntegerField()
    name = serializers.CharField()
    user_completed_at = serializers.DateTimeField()
    photo = serializers.SerializerMethodField()
    questions = QuestionUserStatSerializer(many=True)

    def get_photo(self, obj: dict) -> str | None:
        request = self.context.get("request")
        if request and obj.get("photo"):
            return request.build_absolute_uri(obj["photo"].url)


class SurveyStatUserSerializer(serializers.Serializer):
    uuid = serializers.UUIDField()
    name = serializers.CharField()
    status = serializers.CharField()
    users = UserStatSerializer(many=True)


class PaginatedSurveyUserStatResponseSerializer(serializers.Serializer):
    count = serializers.IntegerField()
    total_pages = serializers.IntegerField()
    results = SurveyStatUserSerializer()
