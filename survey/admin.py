from typing import Any

from django.contrib import admin
from nested_inline.admin import NestedModelAdmin, NestedStackedInline

from survey.models import AnswerOption, Question, Survey, UserAnswer, UserResponse


class AnswerOptionInline(NestedStackedInline):
    model = AnswerOption
    extra = 0


class QuestionInline(NestedStackedInline):
    model = Question
    extra = 0
    inlines = [AnswerOptionInline]


class UserAnswerInline(NestedStackedInline):
    model = UserAnswer
    exclude = ("answer_option",)
    readonly_fields = ("question",)
    extra = 0


class SurveyAdmin(NestedModelAdmin):
    model = Survey
    inlines = [QuestionInline]
    list_display = (
        "name",
        "status",
        "display_owners",
        "end_date",
        "is_anonymous",
        "display_questions",
    )
    list_display_links = ("name",)
    exclude = ("uuid",)
    ordering = ("name",)

    def display_questions(self, obj: Any) -> str:
        """Отображает вопросы, которые придадлежат опросу."""
        return (
            ", ".join([question.name for question in obj.questions.all()])
            if obj.questions.exists()
            else "-"
        )

    def display_owners(self, obj: Any) -> str:
        """Отображает владельцев опроса."""
        return ", ".join([owner.username for owner in obj.owner_user_ids.all()])

    display_owners.short_description = "Owners"


admin.site.register(Survey, SurveyAdmin)


class QuestionAdmin(admin.ModelAdmin):
    model = Question
    list_display = (
        "name",
        "survey",
        "type",
        "seq_id",
        "is_active",
    )
    readonly_fields = (
        "name",
        "survey",
        "type",
        "seq_id",
        "is_active",
    )
    list_display_links = ("name",)
    exclude = ("id",)
    ordering = ("survey",)


admin.site.register(Question, QuestionAdmin)


class AnswerOptionAdmin(admin.ModelAdmin):
    model = AnswerOption
    list_display = (
        "question",
        "seq_id",
        "name",
        "is_active",
    )
    exclude = ("uuid",)
    ordering = ("seq_id",)


admin.site.register(AnswerOption, AnswerOptionAdmin)


class UserResponseAdmin(admin.ModelAdmin):
    model = UserResponse
    inlines = [UserAnswerInline]
    list_display = (
        "status",
        "survey",
        "user",
    )

    exclude = ("uuid",)
    ordering = ("survey",)


admin.site.register(UserResponse, UserResponseAdmin)


class UserAnswerAdmin(admin.ModelAdmin):
    model = UserAnswer
    list_display = (
        "question",
        "answer_option",
        "text_answer",
        "user_response",
    )
    readonly_fields = ("text_answer",)
    list_filter = ("user_response__user",)

    exclude = ("uuid",)
    ordering = ("question",)


admin.site.register(UserAnswer, UserAnswerAdmin)
