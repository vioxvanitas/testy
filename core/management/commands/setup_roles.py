from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand
from django.utils.crypto import get_random_string

from core.models import UserProfile
from core.permissions import ROLE_GROUPS


class Command(BaseCommand):
    help = "Create the five role groups and, if no superuser exists, a first administrator account."

    def add_arguments(self, parser):
        parser.add_argument("--admin-username", default="admin")
        parser.add_argument("--skip-admin", action="store_true", help="Only create the groups.")

    def handle(self, *args, **opts):
        for name in ROLE_GROUPS:
            _, created = Group.objects.get_or_create(name=name)
            self.stdout.write(f"{'Created' if created else 'Exists '}  group {name}")
        User = get_user_model()
        if opts["skip_admin"] or User.objects.filter(is_superuser=True).exists():
            return
        password = get_random_string(14, "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789")
        user = User.objects.create_superuser(opts["admin_username"], password=password)
        user.groups.add(Group.objects.get(name="Administrator"))
        UserProfile.objects.create(user=user, must_change_password=True)
        self.stdout.write(self.style.SUCCESS(
            f"Created administrator '{user.username}' with temporary password: {password}\n"
            "You will be asked to change it at first login. Store it safely; it is not shown again."))
