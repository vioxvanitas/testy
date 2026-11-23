import csv
import io
from decimal import Decimal

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import Group
from django.contrib.auth.views import PasswordChangeView
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import Count, DecimalField, Max, OuterRef, ProtectedError, Q, RestrictedError, Subquery, Sum, Value
from django.db.models.functions import Coalesce
from django.forms import modelformset_factory
from django.http import Http404, HttpResponse, HttpResponseForbidden
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.decorators.http import require_POST

from . import services
from .forms import EnrollmentForm, GradeEntryForm, PaymentForm, StudentForm
from .models import (
    AssessmentItem, AuditLog, ClassSchedule, CurriculumSubject, Department, Enrollment, EnrollmentSubject, Fee,
    Grade, Instructor, Payment, Program, Room, SchoolTerm, Section, Student, Subject, SubjectOffering, UserProfile,
)
from .permissions import (
    can_access, can_manage, can_record_payments, has_role, is_instructor, is_student,
)

PAGE_SIZE = 15


# ---------------------------------------------------------------- helpers
def guard(request, module):
    if not can_access(request.user, module):
        return HttpResponseForbidden("You do not have permission to access this module.")
    return None


def paginate(request, items, per_page=PAGE_SIZE):
    """Return (page, querystring_without_page) so filter links survive paging without duplicate page= params."""
    page = Paginator(items, per_page).get_page(request.GET.get("page"))
    params = request.GET.copy()
    params.pop("page", None)
    return page, params.urlencode()


def audit(request, action, obj):
    AuditLog.objects.create(user=request.user, action=action, object_type=type(obj).__name__, object_id=obj.pk)


def flash_errors(request, exc):
    for msg in exc.messages:
        messages.error(request, msg)


def money(value):
    return f"₱{value:,.2f}"


def with_financials(qs):
    """Annotate enrollments with assessed/paid totals in one query (no per-row queries, no join inflation)."""
    dec = DecimalField(max_digits=12, decimal_places=2)
    assessed = AssessmentItem.objects.filter(enrollment=OuterRef("pk")).order_by().values("enrollment") \
        .annotate(t=Sum("amount")).values("t")
    paid = Payment.objects.filter(enrollment=OuterRef("pk"), is_void=False).order_by().values("enrollment") \
        .annotate(t=Sum("amount")).values("t")
    return qs.annotate(
        assessed_total=Coalesce(Subquery(assessed, output_field=dec), Value(Decimal("0.00")), output_field=dec),
        paid_amount=Coalesce(Subquery(paid, output_field=dec), Value(Decimal("0.00")), output_field=dec),
    )


def finance_rows(qs):
    return [{"enrollment": e, "assessed": e.assessed_total, "paid": e.paid_amount,
             "balance": e.assessed_total - e.paid_amount} for e in qs]


def own_student(user):
    return Student.objects.filter(user=user).first()


def get_enrollment_for(request, pk):
    """Fetch an enrollment, enforcing that students only reach their own."""
    enrollment = get_object_or_404(Enrollment.objects.select_related("student__program", "term"), pk=pk)
    if is_student(request.user) and enrollment.student.user_id != request.user.id:
        raise PermissionDenied("You can only view your own enrollment.")
    return enrollment


# ---------------------------------------------------------------- accounts
def create_student_portal_account(student):
    """Create a student login: username = Student ID, surname as the temporary password to change.

    Returns (created, message, temporary_password).
    """
    if student.user_id:
        return False, "This student is already linked to a login account.", None
    User = get_user_model()
    if User.objects.filter(username=student.student_id).exists():
        return False, f"Username {student.student_id} already exists. Resolve the existing account before linking it.", None
    temp_password = student.last_name
    with transaction.atomic():
        user = User.objects.create_user(
            username=student.student_id, password=temp_password, email=student.email or "",
            first_name=student.first_name, last_name=student.last_name,
        )
        group, _ = Group.objects.get_or_create(name="Student")
        user.groups.add(group)
        UserProfile.objects.create(user=user, must_change_password=True)
        student.user = user
        student.save(update_fields=["user"])
    return True, (f"Student portal account created. Username: {student.student_id} · temporary password: "
                  f"{temp_password} (shown once — give it to the student; they must change it at first login)."), temp_password


class ForcedPasswordChangeView(PasswordChangeView):
    template_name = "registration/password_change_form.html"
    success_url = reverse_lazy("password_change_done")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["must_change"] = UserProfile.objects.filter(user=self.request.user, must_change_password=True).exists()
        return ctx

    def form_valid(self, form):
        if form.cleaned_data["old_password"] == form.cleaned_data["new_password1"]:
            form.add_error("new_password1", "Choose a password different from your current one.")
            return self.form_invalid(form)
        response = super().form_valid(form)
        UserProfile.objects.filter(user=self.request.user).update(must_change_password=False)
        return response


# ---------------------------------------------------------------- dashboard
@login_required
def dashboard(request):
    user = request.user
    ctx = {"cards": [], "student": None, "term": None, "enrollment": None, "balance": None}
    term = SchoolTerm.objects.filter(is_open=True).order_by("-school_year", "-id").first()
    ctx["term"] = term
    if is_student(user):
        student = own_student(user)
        ctx["student"] = student
        if student and term:
            enrollment = with_financials(Enrollment.objects.filter(student=student, term=term)).first()
            ctx["enrollment"] = enrollment
            if enrollment:
                ctx["balance"] = enrollment.assessed_total - enrollment.paid_amount
        return render(request, "core/dashboard.html", ctx)
    cards = ctx["cards"]
    if has_role(user, "Administrator", "Registrar"):
        cards += [("Students", Student.objects.count()),
                  ("Applications", Enrollment.objects.count()),
                  ("Pending review", Enrollment.objects.filter(status="PENDING").count())]
    if has_role(user, "Administrator", "Cashier"):
        total = Payment.objects.filter(is_void=False).aggregate(v=Sum("amount"))["v"] or Decimal("0")
        cards.append(("Payments recorded", money(total)))
    if has_role(user, "Instructor"):
        cards += [("My classes", SubjectOffering.objects.filter(instructor__user=user, section__term__is_open=True).count()),
                  ("Grade sheets open", EnrollmentSubject.objects.filter(
                      offering__instructor__user=user, enrollment__status="ENROLLED").exclude(grade__is_locked=True).count())]
    return render(request, "core/dashboard.html", ctx)


# ---------------------------------------------------------------- students
@login_required
def student_list(request):
    denied = guard(request, "students")
    if denied:
        return denied
    terms = SchoolTerm.objects.order_by("-school_year", "-id")
    term_id = request.GET.get("term", "")
    selected_term = terms.filter(pk=term_id).first() if term_id.isdigit() else terms.filter(is_open=True).first()
    status = request.GET.get("status", "")
    students = Student.objects.select_related("program", "program__department")
    if is_student(request.user):
        students = students.filter(user=request.user)
    search = request.GET.get("q", "").strip()
    if search:
        students = students.filter(
            Q(student_id__icontains=search) | Q(first_name__icontains=search)
            | Q(middle_name__icontains=search) | Q(last_name__icontains=search) | Q(email__icontains=search))
    program, year = request.GET.get("program", ""), request.GET.get("year", "")
    if program.isdigit():
        students = students.filter(program_id=program)
    if year.isdigit():
        students = students.filter(year_level=year)
    if selected_term:
        enrolled_ids = Enrollment.objects.filter(term=selected_term, status="ENROLLED").values("student_id")
        if status == "ENROLLED":
            students = students.filter(pk__in=enrolled_ids)
        elif status == "UNENROLLED":
            students = students.exclude(pk__in=enrolled_ids)

    # Paginate the queryset first, then fetch enrollments for this page only, in ONE query.
    page_obj, querystring = paginate(request, students.order_by("student_id"))
    page_students = list(page_obj.object_list)
    by_student = {}
    if selected_term:
        by_student = {e.student_id: e for e in Enrollment.objects.filter(term=selected_term, student__in=page_students)}
    rows = []
    for s in page_students:
        enrollment = by_student.get(s.pk)
        rows.append({"student": s, "enrollment": enrollment,
                     "status": ("ENROLLED" if enrollment and enrollment.status == "ENROLLED" else "UNENROLLED") if selected_term else ""})
    max_years = Program.objects.aggregate(m=Max("years_to_complete"))["m"] or 4
    return render(request, "core/student_list.html", {
        "rows": rows, "page_obj": page_obj, "querystring": querystring, "terms": terms,
        "selected_term": selected_term, "programs": Program.objects.order_by("code"), "search": search,
        "selected_status": status, "selected_program": program, "selected_year": year,
        "years": range(1, max_years + 1), "can_manage": can_manage(request.user),
    })


@login_required
def student_create(request):
    denied = guard(request, "students")
    if denied:
        return denied
    if not can_manage(request.user):
        return HttpResponseForbidden("Only the Administrator or Registrar can register students.")
    form = StudentForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            student = form.save()
            audit(request, "Created student", student)
            created, account_message, _ = create_student_portal_account(student)
        (messages.success if created else messages.warning)(request, account_message)
        messages.success(request, f"Student registered. ID: {student.student_id}")
        return redirect("student_detail", pk=student.pk)
    return render(request, "core/form.html", {"form": form, "title": "Student Registration", "cancel_url": reverse("student_list")})


@login_required
def student_detail(request, pk):
    denied = guard(request, "students")
    if denied:
        return denied
    student = get_object_or_404(Student.objects.select_related("program__department"), pk=pk)
    if is_student(request.user) and student.user_id != request.user.id:
        return HttpResponseForbidden("You can only view your own student record.")
    enrollments = with_financials(student.enrollments.select_related("term").order_by("-submitted_at"))
    return render(request, "core/student_detail.html", {
        "student": student, "rows": finance_rows(enrollments), "can_manage": can_manage(request.user)})


@login_required
@require_POST
def student_create_portal_account(request, pk):
    denied = guard(request, "students")
    if denied:
        return denied
    if not can_manage(request.user):
        return HttpResponseForbidden("Only the Administrator or Registrar can create student portal accounts.")
    student = get_object_or_404(Student, pk=pk)
    created, account_message, _ = create_student_portal_account(student)
    if created:
        audit(request, "Created student portal account", student)
    (messages.success if created else messages.warning)(request, account_message)
    return redirect("student_detail", pk=student.pk)


@login_required
def student_edit(request, pk):
    denied = guard(request, "students")
    if denied:
        return denied
    if not can_manage(request.user):
        return HttpResponseForbidden("Only the Administrator or Registrar can edit student records.")
    student = get_object_or_404(Student, pk=pk)
    form = StudentForm(request.POST or None, instance=student)
    if request.method == "POST" and form.is_valid():
        form.save()
        audit(request, "Updated student", student)
        messages.success(request, "Student record updated.")
        return redirect("student_detail", pk=student.pk)
    return render(request, "core/form.html", {"form": form, "title": "Edit Student", "cancel_url": reverse("student_detail", args=[student.pk])})


@login_required
@require_POST
def student_delete(request, pk):
    denied = guard(request, "students")
    if denied:
        return denied
    if not can_manage(request.user):
        return HttpResponseForbidden("Only the Administrator or Registrar can delete student records.")
    student = get_object_or_404(Student, pk=pk)
    portal_user, student_pk = student.user, student.pk
    try:
        with transaction.atomic():
            student.delete()
            # Remove the now-orphaned portal login (never touch staff/superuser accounts).
            if portal_user and not portal_user.is_staff and not portal_user.is_superuser:
                portal_user.delete()
            AuditLog.objects.create(user=request.user, action="Deleted student", object_type="Student", object_id=student_pk)
        messages.success(request, "Student record deleted.")
    except (ProtectedError, RestrictedError):
        messages.error(request, "This student cannot be deleted because related enrollment or financial records exist. Keep the record instead.")
    return redirect("student_list")


# ---------------------------------------------------------------- academic setup (managed CRUD)
def col(header, path):
    return (header, path)


MANAGED = {
    "colleges": dict(model=Department, module="academics", title="Colleges",
                     columns=[col("Code", "code"), col("Name", "name")], search=["code", "name"]),
    "programs": dict(model=Program, module="academics", title="Academic Programs",
                     columns=[col("Code", "code"), col("Name", "name"), col("College", "department"), col("Years", "years_to_complete")],
                     search=["code", "name", "department__name"], related=["department"]),
    "terms": dict(model=SchoolTerm, module="academics", title="School Terms",
                  columns=[col("Semester", "name"), col("School year", "school_year"), col("Starts", "starts_on"), col("Ends", "ends_on"), col("Open", "is_open")],
                  search=["name", "school_year"]),
    "subjects": dict(model=Subject, module="academics", title="Subjects",
                     columns=[col("Code", "code"), col("Title", "title"), col("Units", "units")], search=["code", "title"]),
    "curriculum": dict(model=CurriculumSubject, module="academics", title="Curriculum Subjects",
                       columns=[col("Program", "program.code"), col("Subject", "subject"), col("Year", "year_level"), col("Semester", "semester")],
                       search=["program__code", "subject__code", "subject__title"], related=["program", "subject"]),
    "instructors": dict(model=Instructor, module="academics", title="Instructors",
                        columns=[col("Instructor", "user"), col("Employee no.", "employee_no"), col("College", "department")],
                        search=["employee_no", "user__username", "user__first_name", "user__last_name"], related=["user", "department"]),
    "rooms": dict(model=Room, module="academics", title="Rooms", columns=[col("Room", "code"), col("Capacity", "capacity")], search=["code"]),
    "sections": dict(model=Section, module="academics", title="Sections",
                     columns=[col("Section", "code"), col("Program", "program.code"), col("Term", "term"), col("Year", "year_level")],
                     search=["code", "program__code"], related=["program", "term"]),
    "offerings": dict(model=SubjectOffering, module="academics", title="Subject Offerings",
                      columns=[col("Subject", "curriculum_subject.subject"), col("Section", "section"), col("Instructor", "instructor"), col("Capacity", "capacity")],
                      search=["curriculum_subject__subject__code", "section__code", "instructor__user__last_name"],
                      related=["curriculum_subject__subject", "section", "instructor__user"]),
    "schedules": dict(model=ClassSchedule, module="schedules", title="Class Schedules",
                      columns=[col("Class", "offering"), col("Day", "get_day_display"), col("Start", "start_time"), col("End", "end_time"), col("Room", "room")],
                      search=["offering__curriculum_subject__subject__code", "offering__section__code", "room__code"],
                      related=["offering__curriculum_subject__subject", "offering__section", "room"]),
    "fees": dict(model=Fee, module="billing", title="Fees",
                 columns=[col("Fee", "name"), col("Amount", "amount"), col("Per unit", "is_per_unit"), col("Active", "active"), col("Discount", "is_discount")],
                 search=["name"]),
    "enrollment-records": dict(model=Enrollment, module="enrollment", title="Enrollment Records",
                               columns=[col("Student", "student"), col("Term", "term"), col("Type", "get_enrollment_type_display"), col("Status", "get_status_display")],
                               search=["student__student_id", "student__last_name", "student__first_name"], related=["student", "term"]),
    "grades-records": dict(model=Grade, module="grades", title="Grade Records",
                           columns=[col("Student", "enrollment_subject.enrollment.student"), col("Subject", "enrollment_subject.offering"),
                                    col("Prelim", "prelim"), col("Midterm", "midterm"), col("Final", "final"), col("Remarks", "remarks"), col("Locked", "is_locked")],
                           search=["enrollment_subject__enrollment__student__last_name", "enrollment_subject__enrollment__student__student_id"],
                           related=["enrollment_subject__enrollment__student", "enrollment_subject__offering__curriculum_subject__subject", "enrollment_subject__offering__section"]),
    "assessment-items": dict(model=AssessmentItem, module="billing", title="Assessment Items",
                             columns=[col("Enrollment", "enrollment"), col("Description", "description"), col("Amount", "amount")],
                             search=["description", "enrollment__student__student_id", "enrollment__student__last_name"], related=["enrollment__student", "enrollment__term"]),
    "enrollment-subjects": dict(model=EnrollmentSubject, module="enrollment", title="Enrolled Subjects",
                                columns=[col("Student", "enrollment.student"), col("Class", "offering")],
                                search=["enrollment__student__student_id", "enrollment__student__last_name", "offering__curriculum_subject__subject__code"],
                                related=["enrollment__student", "offering__curriculum_subject__subject", "offering__section"]),
}
# Enrollment status is changed only through the approval workflow, never through the generic form.
MANAGED_EXCLUDE = {Enrollment: ["submitted_at", "approved_by", "approved_at", "status"]}


def managed_config(slug):
    if slug not in MANAGED:
        raise Http404("Unknown management section")
    return MANAGED[slug]


def cell(obj, path):
    value = obj
    for part in path.split("."):
        value = getattr(value, part, None)
        if callable(value):
            value = value()
        if value is None:
            return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return value


def scope_queryset(user, model, qs):
    """Limit what students and instructors may see in generic lists."""
    if is_student(user):
        if model is Enrollment:
            qs = qs.filter(student__user=user)
        elif model in (EnrollmentSubject, AssessmentItem):
            qs = qs.filter(enrollment__student__user=user)
        elif model is Grade:
            qs = qs.filter(enrollment_subject__enrollment__student__user=user, is_locked=True)
        elif model is ClassSchedule:
            qs = qs.filter(offering__enrollmentsubject__enrollment__student__user=user).distinct()
    elif is_instructor(user):
        if model is Grade:
            qs = qs.filter(enrollment_subject__offering__instructor__user=user)
        elif model is ClassSchedule:
            qs = qs.filter(offering__instructor__user=user)
    return qs


@login_required
def academic_overview(request):
    denied = guard(request, "academics")
    if denied:
        return denied
    counts = {"colleges": Department.objects.count(), "programs": Program.objects.count(),
              "subjects": Subject.objects.count(), "terms": SchoolTerm.objects.count()}
    links = [(slug, cfg["title"]) for slug, cfg in MANAGED.items()]
    return render(request, "core/academic_overview.html", {"counts": counts, "links": links})


@login_required
def managed_list(request, slug):
    cfg = managed_config(slug)
    denied = guard(request, cfg["module"])
    if denied:
        return denied
    model = cfg["model"]
    qs = model.objects.all()
    if cfg.get("related"):
        qs = qs.select_related(*cfg["related"])
    qs = scope_queryset(request.user, model, qs)
    search = request.GET.get("q", "").strip()
    if search and cfg.get("search"):
        cond = Q()
        for f in cfg["search"]:
            cond |= Q(**{f + "__icontains": search})
        qs = qs.filter(cond)
    page_obj, querystring = paginate(request, qs.order_by("pk"))
    rows = [{"obj": o, "cells": [cell(o, path) for _, path in cfg["columns"]]} for o in page_obj.object_list]
    return render(request, "core/managed_list.html", {
        "slug": slug, "title": cfg["title"], "headers": [h for h, _ in cfg["columns"]], "rows": rows,
        "page_obj": page_obj, "querystring": querystring, "search": search, "can_manage": can_manage(request.user)})


@login_required
def managed_form(request, slug, pk=None):
    cfg = managed_config(slug)
    denied = guard(request, cfg["module"])
    if denied:
        return denied
    if not can_manage(request.user):
        return HttpResponseForbidden("Only the Administrator or Registrar can maintain these records.")
    model, title = cfg["model"], cfg["title"]
    instance = get_object_or_404(model, pk=pk) if pk is not None else None
    ModelForm = forms.modelform_factory(model, fields="__all__", exclude=MANAGED_EXCLUDE.get(model, []), widgets={
        "starts_on": forms.DateInput(attrs={"type": "date"}), "ends_on": forms.DateInput(attrs={"type": "date"}),
        "start_time": forms.TimeInput(attrs={"type": "time"}), "end_time": forms.TimeInput(attrs={"type": "time"}),
    })
    form = ModelForm(request.POST or None, instance=instance)
    if request.method == "POST" and form.is_valid():
        obj = form.save()
        AuditLog.objects.create(user=request.user, action=("Updated " if instance else "Created ") + title,
                                object_type=model.__name__, object_id=obj.pk)
        messages.success(request, f"{title[:-1] if title.endswith('s') else title} saved.")
        return redirect("managed_list", slug=slug)
    return render(request, "core/form.html", {"form": form, "title": ("Edit " if instance else "Add ") + title,
                                              "cancel_url": reverse("managed_list", args=[slug])})


@login_required
@require_POST
def managed_delete(request, slug, pk):
    cfg = managed_config(slug)
    denied = guard(request, cfg["module"])
    if denied:
        return denied
    if not can_manage(request.user):
        return HttpResponseForbidden("Only the Administrator or Registrar can delete these records.")
    model = cfg["model"]
    obj = get_object_or_404(model, pk=pk)
    try:
        with transaction.atomic():
            AuditLog.objects.create(user=request.user, action="Deleted " + cfg["title"], object_type=model.__name__, object_id=obj.pk)
            obj.delete()
        messages.success(request, "Record deleted.")
    except (ProtectedError, RestrictedError):
        messages.error(request, "This record is being used by other records and cannot be deleted.")
    return redirect("managed_list", slug=slug)


# ---------------------------------------------------------------- enrollment
@login_required
def enrollment_list(request):
    denied = guard(request, "enrollment")
    if denied:
        return denied
    qs = Enrollment.objects.select_related("student", "term").order_by("-submitted_at")
    if is_student(request.user):
        qs = qs.filter(student__user=request.user)
    status = request.GET.get("status", "")
    if status in dict(Enrollment.STATUS):
        qs = qs.filter(status=status)
    page_obj, querystring = paginate(request, qs, 20)
    return render(request, "core/enrollment_list.html", {
        "page_obj": page_obj, "querystring": querystring, "status": status, "statuses": Enrollment.STATUS,
        "can_manage": can_manage(request.user)})


@login_required
def enrollment_create(request):
    denied = guard(request, "enrollment")
    if denied:
        return denied
    if is_student(request.user) and not own_student(request.user):
        messages.error(request, "Your login is not linked to a student record. Please contact the Registrar.")
        return redirect("dashboard")
    form = EnrollmentForm(request.POST or None, user=request.user)
    if request.method == "POST" and form.is_valid():
        enrollment = form.save()
        audit(request, "Submitted enrollment", enrollment)
        messages.success(request, "Application created. Now choose the subjects to enroll in.")
        return redirect("enrollment_detail", pk=enrollment.pk)
    if not SchoolTerm.objects.filter(is_open=True).exists():
        messages.warning(request, "No school term is open for enrollment.")
    return render(request, "core/form.html", {"form": form, "title": "New Enrollment Application", "cancel_url": reverse("enrollment_list")})


def _can_edit_subjects(user, enrollment):
    owner = is_student(user) and enrollment.student.user_id == user.id
    return enrollment.status == "PENDING" and (can_manage(user) or owner)

def _can_add_subjects(user, enrollment):
    return _can_edit_subjects(user, enrollment) or (enrollment.status == "ENROLLED" and can_manage(user))


@login_required
def enrollment_detail(request, pk):
    denied = guard(request, "enrollment")
    if denied:
        return denied
    enrollment = get_enrollment_with_finance(request, pk)
    user = request.user
    lines = enrollment.subjects.select_related(
        "offering__curriculum_subject__subject", "offering__section", "offering__instructor__user"
    ).prefetch_related("offering__schedules__room")
    chosen = [l.offering_id for l in lines]
    can_edit = _can_edit_subjects(user, enrollment)
    can_add = _can_add_subjects(user, enrollment)
    offerings = []
    show_all = request.GET.get("all") == "1"
    if can_add:
        offerings = SubjectOffering.objects.filter(section__term=enrollment.term).exclude(pk__in=chosen) \
            .select_related("curriculum_subject__subject", "section", "instructor__user") \
            .prefetch_related("schedules__room") \
            .annotate(taken=Count("enrollmentsubject", filter=~Q(enrollmentsubject__enrollment__status="REJECTED"))) \
            .order_by("curriculum_subject__subject__code", "section__code")
        if not show_all:
            offerings = offerings.filter(section__program=enrollment.student.program)
    units = sum((l.offering.curriculum_subject.subject.units for l in lines), Decimal("0"))
    manager = can_manage(user)
    return render(request, "core/enrollment_detail.html", {
        "enrollment": enrollment, "lines": lines, "units": units, "offerings": offerings, "show_all": show_all,
        "can_edit": can_edit, "can_add": can_add, "can_manage": manager,
        "subject_added": request.GET.get("subject_added") if request.GET.get("subject_added") in ("regular", "late") else "",
        "subject_add_failed": request.GET.get("subject_add_failed") == "1",
        "assessed": enrollment.assessed_total, "paid": enrollment.paid_amount,
        "balance": enrollment.assessed_total - enrollment.paid_amount,
        "required": services.required_payment(enrollment) if enrollment.status == "APPROVED" else None,
        "items": enrollment.assessment_items.all(),
        "discounts": Fee.objects.filter(is_discount=True, active=True) if manager and enrollment.status in ("PENDING", "APPROVED") else [],
        "transitions": services.TRANSITIONS[enrollment.status] if manager else set(),
        "can_pay": can_record_payments(user) and enrollment.status in ("APPROVED", "ENROLLED"),
    })


def get_enrollment_with_finance(request, pk):
    get_enrollment_for(request, pk)  # permission + 404 check
    return with_financials(Enrollment.objects.select_related("student__program", "term")).get(pk=pk)


@login_required
@require_POST
def enrollment_add_subject(request, pk):
    denied = guard(request, "enrollment")
    if denied:
        return denied
    enrollment = get_enrollment_for(request, pk)
    if not _can_add_subjects(request.user, enrollment):
        raise PermissionDenied("You cannot change this enrollment.")
    offering = get_object_or_404(SubjectOffering, pk=request.POST.get("offering") or 0)
    late_add = enrollment.status == "ENROLLED"
    try:
        services.add_subject(enrollment, offering, allow_enrolled=late_add)
        audit(request, "Late-added subject" if late_add else "Added subject to enrollment", enrollment)
    except ValidationError as exc:
        flash_errors(request, exc)
        return redirect(f"{reverse('enrollment_detail', args=[pk])}?subject_add_failed=1")
    return redirect(f"{reverse('enrollment_detail', args=[pk])}?subject_added={'late' if late_add else 'regular'}")


@login_required
@require_POST
def enrollment_drop_subject(request, pk, line_pk):
    denied = guard(request, "enrollment")
    if denied:
        return denied
    enrollment = get_enrollment_for(request, pk)
    if not _can_edit_subjects(request.user, enrollment):
        raise PermissionDenied("You cannot change this enrollment.")
    line = get_object_or_404(EnrollmentSubject, pk=line_pk, enrollment=enrollment)
    line.delete()
    audit(request, "Dropped subject from enrollment", enrollment)
    messages.success(request, "Subject removed.")
    return redirect("enrollment_detail", pk=pk)


ACTIONS = {"approve": "APPROVED", "reject": "REJECTED", "enroll": "ENROLLED", "reopen": "PENDING"}


@login_required
@require_POST
def enrollment_action(request, pk):
    denied = guard(request, "enrollment")
    if denied:
        return denied
    if not can_manage(request.user):
        return HttpResponseForbidden("Only the Administrator or Registrar can process enrollments.")
    enrollment = get_enrollment_for(request, pk)
    action = request.POST.get("action", "")
    try:
        if action in ACTIONS:
            services.change_status(enrollment, ACTIONS[action], request.user)
            messages.success(request, f"Enrollment is now {ACTIONS[action].title()}.")
        elif action == "assess":
            if enrollment.status not in ("PENDING", "APPROVED"):
                raise ValidationError("The assessment is final once the student is enrolled.")
            services.generate_assessment(enrollment)
            audit(request, "Regenerated assessment", enrollment)
            messages.success(request, "Assessment regenerated.")
        elif action == "discount":
            services.apply_discount(enrollment, get_object_or_404(Fee, pk=request.POST.get("fee") or 0), request.user)
            messages.success(request, "Discount applied.")
        else:
            raise ValidationError("Unknown action.")
    except ValidationError as exc:
        flash_errors(request, exc)
    return redirect("enrollment_detail", pk=pk)


# ---------------------------------------------------------------- billing
@login_required
def billing_list(request):
    denied = guard(request, "billing")
    if denied:
        return denied
    qs = with_financials(Enrollment.objects.select_related("student", "term")).order_by("-submitted_at")
    if is_student(request.user):
        qs = qs.filter(student__user=request.user)
    search = request.GET.get("q", "").strip()
    if search:
        qs = qs.filter(Q(student__student_id__icontains=search) | Q(student__last_name__icontains=search) | Q(student__first_name__icontains=search))
    page_obj, querystring = paginate(request, qs)
    return render(request, "core/billing_list.html", {
        "rows": finance_rows(page_obj.object_list), "page_obj": page_obj, "querystring": querystring,
        "search": search, "can_pay": can_record_payments(request.user)})


@login_required
def billing_detail(request, pk):
    denied = guard(request, "billing")
    if denied:
        return denied
    get_enrollment_for(request, pk)
    enrollment = with_financials(Enrollment.objects.select_related("student", "term")).get(pk=pk)
    return render(request, "core/billing_detail.html", {
        "enrollment": enrollment, "items": enrollment.assessment_items.all(),
        "payments": enrollment.payments.select_related("recorded_by").order_by("-paid_at"),
        "assessed": enrollment.assessed_total, "paid": enrollment.paid_amount,
        "balance": enrollment.assessed_total - enrollment.paid_amount, "can_pay": can_record_payments(request.user)})


@login_required
def record_payment(request):
    denied = guard(request, "billing")
    if denied:
        return denied
    if not can_record_payments(request.user):
        return HttpResponseForbidden("Only the Cashier or Administrator can record payments.")
    initial = {"enrollment": request.GET["enrollment"]} if request.GET.get("enrollment", "").isdigit() else None
    form = PaymentForm(request.POST or None, initial=initial)
    if request.method == "POST" and form.is_valid():
        payment = form.save(commit=False)
        payment.recorded_by = request.user
        payment.save()
        audit(request, "Recorded payment", payment)
        messages.success(request, f"Payment recorded. Official receipt {payment.receipt_no}.")
        return redirect("payment_receipt", pk=payment.pk)
    return render(request, "core/form.html", {"form": form, "title": "Record Payment", "cancel_url": reverse("billing_list")})


@login_required
def payment_receipt(request, pk):
    denied = guard(request, "billing")
    if denied:
        return denied
    payment = get_object_or_404(Payment.objects.select_related("enrollment__student__program", "enrollment__term", "recorded_by"), pk=pk)
    if is_student(request.user) and payment.enrollment.student.user_id != request.user.id:
        raise PermissionDenied("You can only view your own receipts.")
    return render(request, "core/receipt.html", {"payment": payment, "balance": services.balance(payment.enrollment)})


@login_required
@require_POST
def payment_void(request, pk):
    denied = guard(request, "billing")
    if denied:
        return denied
    if not can_record_payments(request.user):
        return HttpResponseForbidden("Only the Cashier or Administrator can void payments.")
    payment = get_object_or_404(Payment, pk=pk)
    try:
        services.void_payment(payment, request.user, request.POST.get("reason", ""))
        messages.success(request, f"Receipt {payment.receipt_no} voided.")
    except ValidationError as exc:
        flash_errors(request, exc)
    return redirect("billing_detail", pk=payment.enrollment_id)


# ---------------------------------------------------------------- schedules & grades
@login_required
def schedule_list(request):
    denied = guard(request, "schedules")
    if denied:
        return denied
    qs = ClassSchedule.objects.select_related(
        "room", "offering__curriculum_subject__subject", "offering__instructor__user", "offering__section"
    ).order_by("day", "start_time")
    qs = scope_queryset(request.user, ClassSchedule, qs)
    page_obj, querystring = paginate(request, qs, 25)
    return render(request, "core/schedule_list.html", {"page_obj": page_obj, "querystring": querystring})


@login_required
def grade_list(request):
    denied = guard(request, "grades")
    if denied:
        return denied
    qs = Grade.objects.select_related(
        "enrollment_subject__enrollment__student", "enrollment_subject__offering__curriculum_subject__subject")
    qs = scope_queryset(request.user, Grade, qs).order_by("enrollment_subject__enrollment__student__last_name")
    page_obj, querystring = paginate(request, qs, 25)
    my_offerings = []
    if is_instructor(request.user) or can_manage(request.user):
        offerings = SubjectOffering.objects.filter(section__term__is_open=True) \
            .select_related("curriculum_subject__subject", "section", "instructor__user")
        if not can_manage(request.user):
            offerings = offerings.filter(instructor__user=request.user)
        my_offerings = offerings.order_by("curriculum_subject__subject__code")
    return render(request, "core/grade_list.html", {
        "page_obj": page_obj, "querystring": querystring, "my_offerings": my_offerings,
        "student_view": is_student(request.user)})


GradeFormSet = modelformset_factory(Grade, form=GradeEntryForm, extra=0)


@login_required
def class_grades(request, pk):
    """Grade sheet for one class: only that class's instructor (or an Administrator/Registrar) may encode."""
    denied = guard(request, "grades")
    if denied:
        return denied
    offering = get_object_or_404(SubjectOffering.objects.select_related(
        "curriculum_subject__subject", "section", "instructor__user"), pk=pk)
    user = request.user
    if not (can_manage(user) or (is_instructor(user) and offering.instructor.user_id == user.id)):
        raise PermissionDenied("You can only encode grades for your own classes.")
    lines = EnrollmentSubject.objects.filter(offering=offering, enrollment__status="ENROLLED")
    have = set(Grade.objects.filter(enrollment_subject__in=lines).values_list("enrollment_subject_id", flat=True))
    Grade.objects.bulk_create([Grade(enrollment_subject=l) for l in lines if l.pk not in have])
    base = Grade.objects.filter(enrollment_subject__in=lines).select_related("enrollment_subject__enrollment__student") \
        .order_by("enrollment_subject__enrollment__student__last_name")
    unlocked = base.filter(is_locked=False)
    formset = GradeFormSet(request.POST or None, queryset=unlocked)
    if request.method == "POST" and formset.is_valid():
        formset.save()
        if "lock" in request.POST:
            incomplete = Grade.objects.filter(pk__in=unlocked.values("pk")).filter(
                Q(prelim__isnull=True) | Q(midterm__isnull=True) | Q(final__isnull=True))
            if incomplete.exists():
                messages.error(request, "Every student needs prelim, midterm and final grades before the sheet can be submitted. Grades were saved.")
            else:
                Grade.objects.filter(pk__in=unlocked.values("pk")).update(is_locked=True)
                audit(request, "Submitted and locked grades", offering)
                messages.success(request, "Grades submitted and locked. Students can now see them.")
        else:
            audit(request, "Saved grades", offering)
            messages.success(request, "Grades saved.")
        return redirect("class_grades", pk=pk)
    return render(request, "core/class_grades.html", {
        "offering": offering, "formset": formset, "locked": base.filter(is_locked=True)})


# ---------------------------------------------------------------- reports
def safe_cell(value):
    """Neutralise spreadsheet formula injection in exported text."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + value
    return value


EXPORT_HEADER = ["Student ID", "Student", "College", "Program", "Year", "Term", "Status"]


def export_rows(enrollments):
    for e in enrollments:
        s = e.student
        yield [s.student_id, f"{s.last_name}, {s.first_name}", s.program.department.code, s.program.code,
               s.year_level, str(e.term), e.get_status_display()]


def _export_csv(rows):
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="enrollment_report.csv"'
    response.write("\ufeff")  # so Excel reads UTF-8 correctly
    writer = csv.writer(response)
    writer.writerow(EXPORT_HEADER)
    for r in rows:
        writer.writerow([safe_cell(v) for v in r])
    return response


def _export_xlsx(rows):
    from openpyxl import Workbook
    from openpyxl.styles import Font
    wb = Workbook()
    ws = wb.active
    ws.title = "Enrollment"
    ws.append(EXPORT_HEADER)
    for c in ws[1]:
        c.font = Font(bold=True)
    for r in rows:
        ws.append(r)
        for c in ws[ws.max_row]:
            if isinstance(c.value, str) and c.value[:1] in ("=", "+", "-", "@"):
                c.data_type = "s"  # store as text, never as a formula
    for letter, width in zip("ABCDEFG", (14, 30, 10, 12, 6, 24, 12)):
        ws.column_dimensions[letter].width = width
    buf = io.BytesIO()
    wb.save(buf)
    response = HttpResponse(buf.getvalue(), content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    response["Content-Disposition"] = 'attachment; filename="enrollment_report.xlsx"'
    return response


def _export_pdf(rows):
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), title="Enrollment report")
    table = Table([EXPORT_HEADER] + [[str(v) for v in r] for r in rows], repeatRows=1)
    table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#c84e08")), ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTSIZE", (0, 0), (-1, -1), 8), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#fff0e2")])]))
    styles = getSampleStyleSheet()
    doc.build([Paragraph("Enrollment report", styles["Title"]),
               Paragraph(f"Generated {timezone.localtime():%Y-%m-%d %H:%M}", styles["Normal"]), table])
    response = HttpResponse(buf.getvalue(), content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="enrollment_report.pdf"'
    return response


@login_required
def reports(request):
    denied = guard(request, "reports")
    if denied:
        return denied
    enrollments = Enrollment.objects.select_related("student__program__department", "term")
    get = request.GET
    if get.get("term", "").isdigit():
        enrollments = enrollments.filter(term_id=get["term"])
    if get.get("program", "").isdigit():
        enrollments = enrollments.filter(student__program_id=get["program"])
    if get.get("college", "").isdigit():
        enrollments = enrollments.filter(student__program__department_id=get["college"])
    exporters = {"csv": _export_csv, "xlsx": _export_xlsx, "pdf": _export_pdf}
    fmt = get.get("format")
    if fmt in exporters:
        return exporters[fmt](export_rows(enrollments.order_by("term_id", "student__student_id")))
    summary = enrollments.order_by().values("term__name", "term__school_year").annotate(
        total=Count("id"), enrolled=Count("id", filter=Q(status="ENROLLED")), pending=Count("id", filter=Q(status="PENDING")))
    collections = Payment.objects.filter(is_void=False, enrollment__in=enrollments).aggregate(t=Sum("amount"))["t"] or Decimal("0")
    return render(request, "core/reports.html", {
        "summary": summary, "collections": collections, "terms": SchoolTerm.objects.order_by("-school_year", "-id"),
        "programs": Program.objects.order_by("code"), "colleges": Department.objects.order_by("code"),
        "sel": {k: get.get(k, "") for k in ("term", "program", "college")}, "querystring": get.urlencode()})


# ---------------------------------------------------------------- audit log
@login_required
def audit_log(request):
    denied = guard(request, "audit")
    if denied:
        return denied
    qs = AuditLog.objects.select_related("user")
    q, who = request.GET.get("q", "").strip(), request.GET.get("user", "").strip()
    if q:
        qs = qs.filter(Q(action__icontains=q) | Q(object_type__icontains=q))
    if who:
        qs = qs.filter(user__username__icontains=who)
    page_obj, querystring = paginate(request, qs, 30)
    return render(request, "core/audit_log.html", {"page_obj": page_obj, "querystring": querystring, "q": q, "who": who})
