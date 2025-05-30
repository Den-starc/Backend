from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import AnonymousUser
from django.db.models import QuerySet
from rest_framework.permissions import AllowAny, BasePermission, IsAuthenticated

from survey.models import Survey


class PermissionManager:
    @staticmethod
    def check(
        user: AbstractBaseUser | AnonymousUser,
        action: str,
        queryset: QuerySet[Survey],
        uuid: str | None,
    ) -> list[BasePermission]:
        if user.is_anonymous and action in {"retrieve", "complete", "create"} and uuid:
            survey = queryset.filter(uuid=uuid).first()
            if survey and survey.is_anonymous:
                return [AllowAny()]
        return [IsAuthenticated()]
