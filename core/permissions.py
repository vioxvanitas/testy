from django.contrib.auth.decorators import user_passes_test

ROLE_GROUPS = {
    "Administrator": {"dashboard","students","academics","enrollment","billing","schedules","grades","reports","audit"},
    "Registrar": {"dashboard","students","enrollment","academics","schedules","reports"},
    "Cashier": {"dashboard","billing","reports"},
    "Instructor": {"dashboard","grades","schedules"},
    "Student": {"dashboard","students","enrollment","billing","schedules","grades"},
}
ALL_MODULES = set().union(*ROLE_GROUPS.values())
STAFF_ROLES = {"Administrator", "Registrar", "Cashier", "Instructor"}


def user_groups(user):
    """Group names for the user, fetched once per user object (i.e. once per request)."""
    if not hasattr(user, "_role_names"):
        user._role_names = set(user.groups.values_list("name", flat=True)) if user.is_authenticated else set()
    return user._role_names


def role_required(*allowed):
    def check(user):
        if not user.is_authenticated:
            return False
        groups = user_groups(user)
        if "Student" in groups and not user.is_superuser:
            return "Student" in allowed
        return user.is_superuser or bool(groups & set(allowed))
    return user_passes_test(check)


def has_role(user, *names):
    if not user.is_authenticated:
        return False
    groups = user_groups(user)
    if "Student" in groups and not user.is_superuser:
        return "Student" in names
    return user.is_superuser or bool(groups & set(names))


def modules_for(user):
    if not user.is_authenticated:
        return set()
    groups = user_groups(user)
    if "Student" in groups and not user.is_superuser:
        return set(ROLE_GROUPS["Student"])
    if user.is_superuser:
        return set(ALL_MODULES)
    mods = set()
    for g in groups:
        mods |= ROLE_GROUPS.get(g, set())
    return mods


def can_access(user, module):
    return module in modules_for(user)


def is_student(user):
    """Student accounts do not gain staff access from additional groups."""
    if not user.is_authenticated or user.is_superuser:
        return False
    return "Student" in user_groups(user)


def is_instructor(user):
    return user.is_authenticated and not user.is_superuser and "Student" not in user_groups(user) \
        and "Instructor" in user_groups(user) \
        and not (user_groups(user) & {"Administrator", "Registrar"})


def can_manage(user):
    """Administrator / Registrar: maintain academic and student records, process enrollments."""
    return has_role(user, "Administrator", "Registrar")


def can_record_payments(user):
    """Only Cashier / Administrator may post or void payments. Students never can."""
    return has_role(user, "Administrator", "Cashier")
