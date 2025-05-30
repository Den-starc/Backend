from django.urls import include, path
from rest_framework.routers import DefaultRouter

from survey.api.views import (
    AnswerOptionViewSet,
    QuestionViewSet,
    SurveyViewSet,
    UserAnswerViewSet,
)

router = DefaultRouter()
router.register("surveys", SurveyViewSet, basename="survey")
router.register("questions", QuestionViewSet, basename="question")
router.register("answer-options", AnswerOptionViewSet, basename="answer-option")
router.register("user-answers", UserAnswerViewSet, basename="user-answer")
urlpatterns = [path("", include(router.urls))]
