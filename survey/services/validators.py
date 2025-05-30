from typing import Iterable

from django.db.models import Q, QuerySet

from survey.domain.entities import UserAnswerRequest
from survey.exceptions import ValidateAnswerError
from survey.models import Question, Survey, UserAnswer, UserResponse
from survey.services.interface import IQuestionAnswerValidator, ISurveyValidator


class AnswerValidatorMixin:
    def get_question_type(self, question: str | None) -> None:
        self.question = Question.objects.filter(uuid=question).first()
        self.question_type = self.question.type if self.question else None


class TextAnswerValidator(ISurveyValidator, AnswerValidatorMixin):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        self.get_question_type(user_answer_request.question)
        if self._is_invalid_text_answer(user_answer_request):
            error_message = "Ответ должен содержать текст"
            raise ValidateAnswerError(error_message)

    def _is_invalid_text_answer(self, user_answer_request: UserAnswerRequest) -> str | None:
        """
        Проверяет корректность текстового ответа.
        """
        return (
            self.question_type == Question.QuestionType.TEXT and user_answer_request.answer_option
        )


class TextQuestionAnswerValidatorExtended(IQuestionAnswerValidator):
    @classmethod
    def check(cls, question: Question) -> None:
        answer = question.answers.first()
        cls._check_answer_exists(answer)
        cls._check_answer_text(answer)

    @staticmethod
    def _check_answer_exists(answer: UserAnswer) -> None:
        if not answer:
            raise ValidateAnswerError("Ответ отсутствует")

    @staticmethod
    def _check_answer_text(answer: UserAnswer) -> None:
        if not answer.text_answer:
            raise ValidateAnswerError("Отсутствует текст в поле")


class ChoiceAnswerValidator(ISurveyValidator, AnswerValidatorMixin):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        self.get_question_type(user_answer_request.question)
        if self._is_invalid_choice_answer(user_answer_request):
            error_message = "Ответ должен содержать выбранный вариант"
            raise ValidateAnswerError(error_message)

    def _is_invalid_choice_answer(self, user_answer_request: UserAnswerRequest) -> str | None:
        """
        Проверяет корректность ответа с выбором.
        """
        return self.question_type != Question.QuestionType.TEXT and user_answer_request.text_answer


class SingleChoiceQuestionAnswerValidatorExtended(IQuestionAnswerValidator):
    @classmethod
    def check(cls, question: Question) -> None:
        answers = question.answers.all()
        cls._check_answer_exists(answers)
        cls._check_answer_text(answers)

    @staticmethod
    def _check_answer_exists(answers: QuerySet[UserAnswer]) -> None:
        if not answers.exists():
            raise ValidateAnswerError("Ответ отсутствует")

    @staticmethod
    def _check_answer_text(answers: QuerySet[UserAnswer]) -> None:
        count = answers.count()
        if not count:
            raise ValidateAnswerError("Отсутствует ответ в поле")
        if count > 1:
            raise ValidateAnswerError("Несколько ответов для единичнго поля")


class MultipleChoiceValidatorExtendedQuestion(IQuestionAnswerValidator):
    @classmethod
    def check(cls, question: Question) -> None:
        answers = question.answers.all()
        cls._check_answer_exists(answers)

    @staticmethod
    def _check_answer_exists(answers: UserAnswer) -> None:
        if not answers:
            raise ValidateAnswerError("Ответ отсутствует")


class CompletedSurveyValidator(ISurveyValidator, AnswerValidatorMixin):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        if self._is_survey_completed(user_answer_request):
            error_message = "Опрос уже пройден"
            raise ValidateAnswerError(error_message)

    @staticmethod
    def _is_survey_completed(user_answer_request: UserAnswerRequest) -> bool:
        user_response = Survey.objects.get_user_response(
            user_answer_request.survey,
            user_answer_request.user,
            user_answer_request.user_response_uuid,
        )
        if user_response:
            return user_response.status == UserResponse.Status.COMPLETED
        return False


class IsSurveyActiveValidator(ISurveyValidator):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        if user_answer_request.survey.status != Survey.Status.ACTIVE:
            error_message = "Опрос не активен"
            raise ValidateAnswerError(error_message)


class SurveyNotStartedValidator(ISurveyValidator, AnswerValidatorMixin):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        try:
            UserResponse.objects.get(
                Q(user=user_answer_request.user)
                if user_answer_request.user
                else Q(uuid=user_answer_request.user_response_uuid),
                survey=user_answer_request.survey,
            )
        except UserResponse.DoesNotExist as e:
            error_message = "Пользователь не начал проходить опрос"
            raise ValidateAnswerError(error_message) from e


class OwnerCompletedSurveyValidator(CompletedSurveyValidator):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        survey = user_answer_request.survey
        user = user_answer_request.user
        if user and not survey.is_anonymous and not survey.is_user_owner(user):
            super().check(user_answer_request)


class OwnerSurveyValidator(ISurveyValidator):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        survey = user_answer_request.survey
        user = user_answer_request.user
        if user and not survey.is_user_owner(user):
            error_message = "Не являетесь владельцем опроса"
            raise ValidateAnswerError(error_message)


class IsSurveyStatValidator(ISurveyValidator):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        answer_count = UserAnswer.objects.get_survey_stat(user_answer_request.survey.uuid)
        if not answer_count:
            error_message = "Статистика по опросу отсутствует"
            raise ValidateAnswerError(error_message)


class QuestionAnswersValidator(ISurveyValidator, AnswerValidatorMixin):
    _validator_map: dict[Question.QuestionType, IQuestionAnswerValidator] = {
        Question.QuestionType.TEXT: TextQuestionAnswerValidatorExtended(),
        Question.QuestionType.SINGLE_CHOICE: SingleChoiceQuestionAnswerValidatorExtended(),
        Question.QuestionType.MULTIPLE_CHOICE: MultipleChoiceValidatorExtendedQuestion(),
    }

    def check(self, user_answer_request: UserAnswerRequest) -> None:
        question_error_map = {}
        questions_qs: QuerySet[Question] = Question.objects.get_survey_questions(
            survey=user_answer_request.survey,
            user=user_answer_request.user,
            user_response=user_answer_request.user_response_uuid,
        )

        for question in questions_qs:
            validator = self._validator_map[question.type]
            try:
                validator.check(question)
            except ValidateAnswerError as e:
                question_error_map[str(question.uuid)] = str(e)
        if question_error_map:
            error_msg = "Ошибка валидации ответов"
            raise ValidateAnswerError(message=error_msg, question_error_map=question_error_map)


class UniqueQuestionsValidator(ISurveyValidator):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        questions = user_answer_request.survey.questions.all()
        unique_questions = {q.name for q in questions}
        if len(questions) != len(unique_questions):
            error_message = "Вопросы не должны повторяться"
            raise ValidateAnswerError(error_message)


class UniqueAnswerOptionsQuestionValidator(ISurveyValidator):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        questions = user_answer_request.survey.questions.all()
        for question in questions:
            answer_options = question.answer_options.all()
            unique_answer_options = {ap.name for ap in answer_options}
            if len(answer_options) != len(unique_answer_options):
                error_message = "Варианты ответов не должны повторяться"
                raise ValidateAnswerError(error_message)


class SurveyNameValidator(ISurveyValidator):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        survey = Survey.objects.get(uuid=user_answer_request.survey.uuid)
        if not survey.name:
            error_message = "У опроса отсутствует название"
            raise ValidateAnswerError(error_message)


class SurveyQuestionsValidator(ISurveyValidator):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        questions = Survey.objects.get(uuid=user_answer_request.survey.uuid).questions.all()
        if not questions.exists():
            error_message = "В опросе должен быть хотя бы один вопрос"
            raise ValidateAnswerError(error_message)
        for question in questions:
            if not question.name:
                error_message = "В опросе присутствует вопрос с пустым названием"
                raise ValidateAnswerError(error_message)


class SurveyAnswersValidator(ISurveyValidator):
    def check(self, user_answer_request: UserAnswerRequest) -> None:
        questions = Survey.objects.get(uuid=user_answer_request.survey.uuid).questions.exclude(
            type=Question.QuestionType.TEXT
        )
        for question in questions:
            answer_options = question.answer_options.all()
            if not answer_options.exists():
                error_message = (
                    f"Для вопроса '{question.name}' должен быть хотя бы один вариант ответа"
                )
                raise ValidateAnswerError(error_message)
            for answer in answer_options:
                if not answer.name:
                    error_message = (
                        f"Для вопроса '{question.name}' должны быть заполнены все ответы"
                    )
                    raise ValidateAnswerError(error_message)


class RequestBaseValidator:
    validators: Iterable[ISurveyValidator]

    def validate(self, user_answer_request: UserAnswerRequest) -> None:
        for validator in self.validators:
            validator.check(user_answer_request)


class UserAnswerChecker(RequestBaseValidator):
    validators = [
        IsSurveyActiveValidator(),
        ChoiceAnswerValidator(),
        CompletedSurveyValidator(),
        TextAnswerValidator(),
    ]


class UserResponseChecker(RequestBaseValidator):
    validators = [OwnerCompletedSurveyValidator()]


class CompletedSurveyChecker(RequestBaseValidator):
    validators = [
        CompletedSurveyValidator(),
        SurveyNotStartedValidator(),
        QuestionAnswersValidator(),
    ]


class SurveyStatChecker(RequestBaseValidator):
    validators = [OwnerSurveyValidator(), IsSurveyStatValidator()]


class CreateSurveyChecker(RequestBaseValidator):
    validators = [
        SurveyNameValidator(),
        SurveyQuestionsValidator(),
        SurveyAnswersValidator(),
        UniqueQuestionsValidator(),
        UniqueAnswerOptionsQuestionValidator(),
    ]
