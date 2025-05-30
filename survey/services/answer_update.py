from survey.domain.entities import UserAnswerUpdate
from survey.models import Question, UserAnswer
from survey.services.interface import QuestionStrategy


class TextQuestionStrategy(QuestionStrategy):
    def handle_answer(self, user_answer_update: UserAnswerUpdate) -> None:
        user_answers = UserAnswer.objects.filter(
            user_response=user_answer_update.user_response,
            question=user_answer_update.question,
        )

        answer = user_answers.first()
        if answer:
            self._perform_answer_update(answer, user_answer_update.text_answer)
        else:
            self._perform_answer_create(user_answer_update)

    @staticmethod
    def _perform_answer_update(answer: UserAnswer, text_answer: str | None) -> None:
        if text_answer:
            answer.text_answer = text_answer
            answer.save(update_fields=["text_answer"])
        else:
            answer.delete()

    @staticmethod
    def _perform_answer_create(user_answer_update: UserAnswerUpdate) -> None:
        if user_answer_update.text_answer:
            UserAnswer.objects.create(
                user_response=user_answer_update.user_response,
                question=user_answer_update.question,
                text_answer=user_answer_update.text_answer,
            )


class SingleChoiceQuestionStrategy(QuestionStrategy):
    def handle_answer(self, user_answer_update: UserAnswerUpdate) -> None:
        user_answers = UserAnswer.objects.filter(
            user_response=user_answer_update.user_response,
            question=user_answer_update.question,
        )

        answer = user_answers.first()
        if answer:
            answer.answer_option = user_answer_update.answer_option
            answer.save(update_fields=["answer_option"])
        else:
            UserAnswer.objects.create(
                user_response=user_answer_update.user_response,
                question=user_answer_update.question,
                answer_option=user_answer_update.answer_option,
            )


class MultipleChoiceQuestionStrategy(QuestionStrategy):
    def handle_answer(self, user_answer_update: UserAnswerUpdate) -> None:
        user_answers = UserAnswer.objects.filter(
            user_response=user_answer_update.user_response,
            question=user_answer_update.question,
        )

        existing_answer = user_answers.filter(
            answer_option=user_answer_update.answer_option
        ).first()
        if existing_answer:
            existing_answer.delete()
        else:
            UserAnswer.objects.create(
                user_response=user_answer_update.user_response,
                question=user_answer_update.question,
                answer_option=user_answer_update.answer_option,
            )


class QuestionHandlerFactory:
    _strategies = {
        Question.QuestionType.TEXT: TextQuestionStrategy(),
        Question.QuestionType.SINGLE_CHOICE: SingleChoiceQuestionStrategy(),
        Question.QuestionType.MULTIPLE_CHOICE: MultipleChoiceQuestionStrategy(),
    }

    @classmethod
    def get_strategy(cls, question_type: Question.QuestionType) -> QuestionStrategy:
        strategy = cls._strategies.get(question_type)
        if not strategy:
            error_message = f"Неподдерживаемый тип вопроса: {question_type}"
            raise ValueError(error_message)
        return strategy
