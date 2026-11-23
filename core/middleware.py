from django.shortcuts import redirect
from django.urls import reverse

from .models import UserProfile


class ForcePasswordChangeMiddleware:
    """Send users with a temporary password to the change-password page until they change it."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = request.user
        if user.is_authenticated and not self._exempt(request.path):
            if UserProfile.objects.filter(user=user, must_change_password=True).exists():
                return redirect("password_change")
        return self.get_response(request)

    @staticmethod
    def _exempt(path):
        return path in {
            reverse("password_change"), reverse("password_change_done"),
            reverse("logout"), "/admin/logout/",
        } or path.startswith("/static/")
