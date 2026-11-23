"""Business rules for enrollment: validation, assessment, payments and the status workflow."""
from decimal import Decimal, ROUND_HALF_UP

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import (
    AssessmentItem, AuditLog, ClassSchedule, Enrollment, EnrollmentSubject, Fee, Grade, Payment, SubjectOffering,
)

CENT = Decimal("0.01")


def _q(value):
    return Decimal(value).quantize(CENT, rounding=ROUND_HALF_UP)


# ---------- money ----------
def total_units(enrollment):
    lines = enrollment.subjects.select_related("offering__curriculum_subject__subject")
    return sum((l.offering.curriculum_subject.subject.units for l in lines), Decimal("0"))


def assessed_total(enrollment):
    return enrollment.assessment_items.aggregate(v=Sum("amount"))["v"] or Decimal("0")


def paid_total(enrollment):
    """Posted payments only; voided payments never count."""
    return enrollment.payments.filter(is_void=False).aggregate(v=Sum("amount"))["v"] or Decimal("0")


def balance(enrollment):
    return assessed_total(enrollment) - paid_total(enrollment)


def required_payment(enrollment):
    """Amount that must be paid before the enrollment can be marked Enrolled (ENROLLMENT_MIN_PAYMENT_PERCENT)."""
    percent = Decimal(str(getattr(settings, "ENROLLMENT_MIN_PAYMENT_PERCENT", 50)))
    return _q(max(assessed_total(enrollment), Decimal("0")) * percent / 100)


@transaction.atomic
def generate_assessment(enrollment):
    """Tuition and fees = per-unit fees x enrolled units + fixed fees. Applied discounts are kept."""
    units = total_units(enrollment)
    enrollment.assessment_items.filter(fee__is_discount=False).delete()
    items = []
    for fee in Fee.objects.filter(active=True, is_discount=False).order_by("id"):
        if fee.is_per_unit:
            amount = _q(fee.amount * units)
            desc = f"{fee.name} ({units:g} units × ₱{fee.amount:,.2f})"
        else:
            amount = fee.amount
            desc = fee.name
        items.append(AssessmentItem(enrollment=enrollment, fee=fee, description=desc[:150], amount=amount))
    AssessmentItem.objects.bulk_create(items)
    return items


@transaction.atomic
def apply_discount(enrollment, fee, user):
    if not fee.is_discount:
        raise ValidationError("That fee is not marked as a discount/scholarship.")
    if enrollment.status not in ("PENDING", "APPROVED"):
        raise ValidationError("Discounts can only be applied before the student is enrolled.")
    if enrollment.assessment_items.filter(fee=fee).exists():
        raise ValidationError("This discount is already applied.")
    AssessmentItem.objects.create(enrollment=enrollment, fee=fee, description=f"{fee.name} (discount)"[:150], amount=-fee.amount)
    log(user, "Applied discount", enrollment)


# ---------- subject cart validation ----------
def passed_subject_ids(student):
    grades = Grade.objects.filter(enrollment_subject__enrollment__student=student) \
        .select_related("enrollment_subject__offering__curriculum_subject")
    return {g.enrollment_subject.offering.curriculum_subject.subject_id for g in grades if g.passed}


def find_schedule_conflict(enrollment, offering):
    """Return the existing class this offering overlaps with, or None."""
    new_slots = list(offering.schedules.all())
    if not new_slots:
        return None
    existing = ClassSchedule.objects.filter(offering__enrollmentsubject__enrollment=enrollment) \
        .select_related("offering__curriculum_subject__subject")
    for n in new_slots:
        for e in existing:
            if e.day == n.day and e.start_time < n.end_time and e.end_time > n.start_time:
                return e
    return None


@transaction.atomic
def add_subject(enrollment, offering, allow_enrolled=False):
    """Validate and add one subject offering to an enrollment. Raises ValidationError with a clear reason."""
    enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
    offering = SubjectOffering.objects.select_for_update().get(pk=offering.pk)
    if enrollment.status != "PENDING" and not (allow_enrolled and enrollment.status == "ENROLLED"):
        raise ValidationError("Subjects can only be changed while the application is Pending.")
    if offering.section.term_id != enrollment.term_id:
        raise ValidationError("That class is not offered in the enrollment's term.")
    subject = offering.curriculum_subject.subject
    if enrollment.subjects.filter(offering__curriculum_subject__subject=subject).exists():
        raise ValidationError(f"{subject.code} is already in this enrollment (possibly under another section).")
    missing = offering.curriculum_subject.prerequisites.exclude(pk__in=passed_subject_ids(enrollment.student))
    if missing.exists():
        raise ValidationError("Missing prerequisite(s): " + ", ".join(m.code for m in missing) + ".")
    if offering.enrolled_count >= offering.capacity:
        raise ValidationError(f"{subject.code} / {offering.section} is full ({offering.capacity} seats).")
    clash = find_schedule_conflict(enrollment, offering)
    if clash:
        raise ValidationError(f"Schedule conflict with {clash.offering.curriculum_subject.subject.code} ({clash}).")
    line = EnrollmentSubject.objects.create(enrollment=enrollment, offering=offering)
    if enrollment.status == "ENROLLED":
        generate_assessment(enrollment)
    return line


# ---------- status workflow ----------
TRANSITIONS = {
    "PENDING": {"APPROVED", "REJECTED"},
    "APPROVED": {"ENROLLED", "REJECTED"},
    "REJECTED": {"PENDING"},
    "ENROLLED": set(),
}


def log(user, action, obj):
    AuditLog.objects.create(user=user, action=action, object_type=type(obj).__name__, object_id=obj.pk)


@transaction.atomic
def change_status(enrollment, new_status, user):
    enrollment = Enrollment.objects.select_for_update().get(pk=enrollment.pk)
    if new_status not in TRANSITIONS[enrollment.status]:
        raise ValidationError(f"An enrollment that is {enrollment.get_status_display()} cannot become {dict(Enrollment.STATUS)[new_status]}.")
    if new_status == "APPROVED":
        if not enrollment.subjects.exists():
            raise ValidationError("Add at least one subject before approving the application.")
        generate_assessment(enrollment)
        enrollment.approved_by, enrollment.approved_at = user, timezone.now()
    elif new_status == "ENROLLED":
        need, paid = required_payment(enrollment), paid_total(enrollment)
        if paid < need:
            raise ValidationError(f"Required payment of ₱{need:,.2f} not yet received (paid ₱{paid:,.2f}).")
    elif new_status in ("REJECTED", "PENDING"):
        enrollment.approved_by, enrollment.approved_at = None, None
    enrollment.status = new_status
    enrollment.save()
    log(user, f"Enrollment set to {new_status.title()}", enrollment)
    return enrollment


@transaction.atomic
def void_payment(payment, user, reason):
    payment = Payment.objects.select_for_update().get(pk=payment.pk)
    if payment.is_void:
        raise ValidationError("This payment is already void.")
    if not reason.strip():
        raise ValidationError("A reason is required to void a payment.")
    payment.is_void, payment.voided_by, payment.voided_at, payment.void_reason = True, user, timezone.now(), reason.strip()[:200]
    payment.save()
    log(user, "Voided payment", payment)
