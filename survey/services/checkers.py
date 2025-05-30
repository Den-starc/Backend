from survey.models import Survey, UserResponse
from survey.services.interface import ISurveyChecker


class CanFinishChecker(ISurveyChecker):
    def check(self, user_response: UserResponse | None) -> bool:
        """
        Проверяет на все ли вопросы опроса были даны ответы, если на каждый вопрос был дан ответ
        чекер возвращает True тем самым позволяя пользователю завершить опрос
        """
        if user_response:
            survey: Survey = user_response.survey
            unique_questions = (
                user_response.user_answers.values_list("question__uuid", flat=True)
                .distinct()
                .count()
            )
            if survey.questions.count() == unique_questions:
                return True
        return False


class IsCompletedChecker(ISurveyChecker):
    def check(self, user_response: UserResponse | None) -> bool:
        """Проверяет завершен ли опрос"""
        if user_response:
            return user_response.status == UserResponse.Status.COMPLETED
        return False
