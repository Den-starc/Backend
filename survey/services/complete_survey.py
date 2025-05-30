from typing import Any

from django.contrib.auth.models import AbstractBaseUser, AnonymousUser
from django.db.models import FloatField, IntegerField, QuerySet, Value
from django.utils import timezone
from rest_framework.request import Request
from rest_framework.utils.serializer_helpers import ReturnDict

from survey.api.serializers import SurveyStatSerializer, SurveyStatUserSerializer
from survey.models import AnswerOption, Question, Survey
from survey.services.factories import DefaultDictFactory, StatSurveyFactory


class CompleteSurveyService:
    @staticmethod
    def complete_survey(
        survey: Survey,
        user: AbstractBaseUser | AnonymousUser | None,
        user_response_uuid: str | None = None,
    ) -> None:
        user_response = Survey.objects.get_user_response(survey, user, user_response_uuid)
        user_response.status = user_response.Status.COMPLETED
        user_response.completed_at = timezone.now()
        user_response.save()


class StatSurveyService:
    @staticmethod
    def stat_survey(answer_counts: QuerySet) -> dict[str, object]:
        result = DefaultDictFactory.create(StatSurveyFactory.survey_factory)

        for item in answer_counts:
            survey_data = {
                "uuid": item["question_id__survey_id__uuid"],
                "name": item["question_id__survey_id__name"],
                "status": item["question_id__survey_id__status"],
            }
            question_data = {
                "uuid": item["question_id__uuid"],
                "name": item["question_id__name"],
                "type": item["question_id__type"],
                "total_count": item["total_count"],
            }
            answer_data = {
                "uuid": item["answer_option_id__uuid"],
                "name": item["answer_option_id__name"],
                "count": item["answer_count"],
                "percentage": item["percentage"],
            }

            survey = result[survey_data["name"]]
            survey.update(
                {
                    "uuid": survey_data["uuid"],
                    "name": survey_data["name"],
                    "status": survey_data["status"],
                }
            )

            question = survey["questions"][question_data["name"]]
            if not question["uuid"]:
                question.update(
                    {
                        "uuid": question_data["uuid"],
                        "name": question_data["name"],
                        "type": question_data["type"],
                        "total_count": question_data["total_count"],
                    }
                )

            question["answers"].append(answer_data)

        final_survey = dict(next(iter(result.values())))
        final_survey["questions"] = list(final_survey["questions"].values())

        serializer = SurveyStatSerializer(final_survey)
        return serializer.data

    @staticmethod
    def stat_user_survey(
        answer_counts: QuerySet, request: Request
    ) -> ReturnDict | dict[str, list[Any] | list[dict[str, str | list]]]:
        result = DefaultDictFactory.create(StatSurveyFactory.survey_user_factory)

        for survey_data in answer_counts:
            survey = result[survey_data.survey_name]
            survey.update(
                {
                    "uuid": survey_data.survey_uuid,
                    "name": survey_data.survey_name,
                    "status": survey_data.survey_status,
                }
            )

            user = survey["users"][survey_data.user_id]
            if not user["uuid"]:
                user.update(
                    {
                        "uuid": survey_data.user_id,
                        "name": survey_data.first_name + " " + survey_data.last_name,
                        "photo": survey_data.user_response.user.thumbnail_photo,
                        "user_completed_at": survey_data.completed_at,
                    }
                )

            question_exists = any(q["uuid"] == survey_data.question_uuid for q in user["questions"])
            if not question_exists:
                user["questions"].append(
                    {
                        "uuid": survey_data.question_uuid,
                        "name": survey_data.question_name,
                        "type": survey_data.question_type,
                        "answers": [],
                    }
                )

            for question in user["questions"]:
                if question["uuid"] == survey_data.question_uuid:
                    if question["type"] == Question.QuestionType.TEXT:
                        answer = {"name": survey_data.text_answer}
                    else:
                        answer = {
                            "uuid": survey_data.answer_uuid,
                            "name": survey_data.answer_name,
                        }

                    question["answers"].append(answer)
                    break
        final_survey = {"users": []}

        if result.values():
            final_survey = dict(next(iter(result.values())))
            final_survey["users"] = list(final_survey["users"].values())

            serializer = SurveyStatUserSerializer(final_survey, context={"request": request})
            return serializer.data
        return final_survey

    @staticmethod
    def add_null_answer_options(survey_stat: dict[str, Any]) -> dict[str, Any]:
        for question in survey_stat.get("questions", []):
            answers_uuid = [
                answer.get("uuid") for answer in question.get("answers") if answer.get("uuid")
            ]
            answers = question.get("answers")
            answers += (
                AnswerOption.objects.filter(question_id=question.get("uuid"))
                .annotate(
                    count=Value(0, output_field=IntegerField()),
                    percentage=Value(0.0, output_field=FloatField()),
                )
                .exclude(uuid__in=answers_uuid)
                .values("uuid", "name", "count", "percentage")
            )

        return survey_stat
