from datetime import time
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse

from . import services
from .models import (
    AssessmentItem, AuditLog, ClassSchedule, CurriculumSubject, Department, Enrollment, EnrollmentSubject, Fee,
    Grade, Instructor, Payment, Program, Room, SchoolTerm, Section, Student, Subject, SubjectOffering, UserProfile,
)
from .permissions import ROLE_GROUPS
from .views import create_student_portal_account

User = get_user_model()
D = Decimal


def mk_user(username, group=None, password="pw-Test-12345", **kw):
    user = User.objects.create_user(username, password=password, **kw)
    if group:
        user.groups.add(Group.objects.get(name=group))
    return user


class BaseData(TestCase):
    @classmethod
    def setUpTestData(cls):
        for name in ROLE_GROUPS:
            Group.objects.create(name=name)
        cls.admin = mk_user("admin1", "Administrator")
        cls.registrar = mk_user("reg1", "Registrar")
        cls.cashier = mk_user("cash1", "Cashier")
        cls.teacher_user = mk_user("teach1", "Instructor", first_name="Tess", last_name="Teacher")
        cls.other_teacher_user = mk_user("teach2", "Instructor")

        cls.college = Department.objects.create(code="CEAT", name="Engineering")
        cls.program = Program.objects.create(department=cls.college, code="BSIT", name="Information Technology")
        cls.term = SchoolTerm.objects.create(name="1st Semester", school_year="2026-2027", is_open=True)
        cls.old_term = SchoolTerm.objects.create(name="2nd Semester", school_year="2025-2026", is_open=False)

        cls.it101 = Subject.objects.create(code="IT101", title="Intro to Computing", units=D("3"))
        cls.it102 = Subject.objects.create(code="IT102", title="Programming 1", units=D("3"))
        cls.it201 = Subject.objects.create(code="IT201", title="Data Structures", units=D("3"))
        cls.cur101 = CurriculumSubject.objects.create(program=cls.program, subject=cls.it101, year_level=1, semester="1st Semester")
        cls.cur102 = CurriculumSubject.objects.create(program=cls.program, subject=cls.it102, year_level=1, semester="1st Semester")
        cls.cur201 = CurriculumSubject.objects.create(program=cls.program, subject=cls.it201, year_level=2, semester="1st Semester")
        cls.cur201.prerequisites.add(cls.it101)

        cls.dept_instructor = Instructor.objects.create(user=cls.teacher_user, employee_no="E1", department=cls.college)
        cls.other_instructor = Instructor.objects.create(user=cls.other_teacher_user, employee_no="E2", department=cls.college)
        cls.section = Section.objects.create(code="IT-1A", term=cls.term, program=cls.program, year_level=1)
        cls.section2 = Section.objects.create(code="IT-2A", term=cls.term, program=cls.program, year_level=2)
        cls.room1 = Room.objects.create(code="R1")
        cls.room2 = Room.objects.create(code="R2")
        cls.off101 = SubjectOffering.objects.create(curriculum_subject=cls.cur101, section=cls.section, instructor=cls.dept_instructor, capacity=2)
        cls.off102 = SubjectOffering.objects.create(curriculum_subject=cls.cur102, section=cls.section, instructor=cls.other_instructor, capacity=40)
        cls.off201 = SubjectOffering.objects.create(curriculum_subject=cls.cur201, section=cls.section2, instructor=cls.other_instructor, capacity=40)
        # IT101 and IT102 overlap Monday 08:00-10:00 / 09:00-11:00 (different rooms and teachers, same section though)
        ClassSchedule.objects.create(offering=cls.off101, day="0", start_time=time(8), end_time=time(10), room=cls.room1)
        ClassSchedule.objects.create(offering=cls.off201, day="1", start_time=time(8), end_time=time(10), room=cls.room2)

        cls.tuition = Fee.objects.create(name="Tuition", amount=D("500.00"), is_per_unit=True)
        cls.misc = Fee.objects.create(name="Misc", amount=D("1000.00"))
        cls.scholar = Fee.objects.create(name="Dean's Lister", amount=D("300.00"), is_discount=True)

        cls.s1 = Student.objects.create(first_name="Ana", last_name="Reyes", program=cls.program)
        cls.s2 = Student.objects.create(first_name="Ben", last_name="Cruz", program=cls.program)
        _, _, _ = create_student_portal_account(cls.s1)
        _, _, _ = create_student_portal_account(cls.s2)
        cls.s1.refresh_from_db(); cls.s2.refresh_from_db()
        for s in (cls.s1, cls.s2):  # skip the forced password change in generic tests
            UserProfile.objects.filter(user=s.user).update(must_change_password=False)

    def login(self, user):
        self.client.force_login(user)


# ---------------------------------------------------------------- bugs from the review
class StudentListTests(BaseData):
    def test_list_works_when_no_term_is_open(self):
        SchoolTerm.objects.update(is_open=False)
        self.login(self.registrar)
        r = self.client.get(reverse("student_list"))
        self.assertEqual(r.status_code, 200)          # used to raise UnboundLocalError
        self.assertContains(r, "No term is open")

    def test_status_is_per_term(self):
        Enrollment.objects.create(student=self.s1, term=self.term, status="ENROLLED")
        Enrollment.objects.create(student=self.s2, term=self.term, status="PENDING")
        self.login(self.registrar)
        r = self.client.get(reverse("student_list"), {"status": "ENROLLED"})
        self.assertEqual([row["student"] for row in r.context["rows"]], [self.s1])
        r = self.client.get(reverse("student_list"), {"status": "UNENROLLED"})
        self.assertEqual([row["student"] for row in r.context["rows"]], [self.s2])   # pending != enrolled
        r = self.client.get(reverse("student_list"), {"term": self.old_term.pk, "status": "ENROLLED"})
        self.assertEqual(list(r.context["rows"]), [])

    def test_pagination_runs_constant_queries_and_keeps_filters(self):
        for i in range(30):
            Student.objects.create(first_name=f"N{i}", last_name="Many", program=self.program)
        self.login(self.registrar)
        from django.db import connection
        from django.test.utils import CaptureQueriesContext
        with CaptureQueriesContext(connection) as ctx:
            r = self.client.get(reverse("student_list"), {"q": "Many", "page": 2})
        self.assertLess(len(ctx), 15, "query count must not grow with the number of students")
        self.assertEqual(len(r.context["rows"]), 15)
        self.assertNotIn("page=", r.context["querystring"])
        self.assertContains(r, "q=Many&amp;page=1")

    def test_bad_filter_values_do_not_crash(self):
        self.login(self.registrar)
        self.assertEqual(self.client.get(reverse("student_list"), {"term": "abc", "program": "x", "year": "y"}).status_code, 200)

    def test_student_sees_only_self(self):
        self.login(self.s1.user)
        r = self.client.get(reverse("student_list"))
        self.assertEqual([row["student"] for row in r.context["rows"]], [self.s1])
        self.assertEqual(self.client.get(reverse("student_detail", args=[self.s2.pk])).status_code, 403)

    def test_student_detail_displays_enrollment_history_and_totals(self):
        enrollment = Enrollment.objects.create(student=self.s1, term=self.term, status="ENROLLED")
        AssessmentItem.objects.create(enrollment=enrollment, fee=self.misc, description="Tuition", amount=D("1500"))
        Payment.objects.create(enrollment=enrollment, amount=D("500"), method="CASH", recorded_by=self.cashier)
        self.login(self.s1.user)

        response = self.client.get(reverse("student_detail", args=[self.s1.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["rows"]), 1)
        self.assertEqual(response.context["rows"][0]["balance"], D("1000"))
        self.assertContains(response, "Enrollment History")
        self.assertContains(response, "₱1500.00")
        self.assertContains(response, "₱500.00")
        self.assertContains(response, "₱1000.00")
        self.assertContains(response, reverse("enrollment_detail", args=[enrollment.pk]))
        self.assertNotContains(response, "No enrollment history yet.")

    def test_student_schedule_shows_classes_in_their_enrollment(self):
        enrollment = Enrollment.objects.create(student=self.s1, term=self.term, status="ENROLLED")
        EnrollmentSubject.objects.create(enrollment=enrollment, offering=self.off101)
        self.login(self.s1.user)

        response = self.client.get(reverse("schedule_list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 1)
        self.assertContains(response, "Intro to Computing")
        self.assertContains(response, "R1")
        self.assertNotContains(response, "No schedules configured.")


class EnrollmentListTests(BaseData):
    def test_enrollment_list_filters_and_paginates_records(self):
        for i in range(20):
            student = Student.objects.create(first_name=f"Page{i}", last_name="Student", program=self.program)
            Enrollment.objects.create(student=student, term=self.term)
        Enrollment.objects.create(student=self.s1, term=self.term)
        Enrollment.objects.create(student=self.s2, term=self.term, status="APPROVED")
        self.login(self.registrar)

        response = self.client.get(reverse("enrollment_list"), {"status": "PENDING", "page": 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["page_obj"].paginator.count, 21)
        self.assertEqual(response.context["page_obj"].number, 2)
        self.assertContains(response, "Page 2 of 2 (21 records)")
        self.assertContains(response, "status=PENDING&amp;page=1")
        self.assertContains(response, "Review / update status")
        self.assertNotContains(response, "No applications.")

    def test_enrollment_records_open_the_registrar_status_workflow(self):
        enrollment = Enrollment.objects.create(student=self.s1, term=self.term)
        services.add_subject(enrollment, self.off101)
        self.login(self.registrar)

        records = self.client.get(reverse("managed_list", args=["enrollment-records"]))
        self.assertContains(records, reverse("enrollment_detail", args=[enrollment.pk]))
        detail = self.client.get(reverse("enrollment_detail", args=[enrollment.pk]))
        self.assertContains(detail, "Approve &amp; generate assessment")

        self.client.post(reverse("enrollment_action", args=[enrollment.pk]), {"action": "approve"})
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, "APPROVED")

    def test_class_add_and_discount_update_the_enrollment_detail_totals(self):
        enrollment = Enrollment.objects.create(student=self.s1, term=self.term)
        self.login(self.registrar)

        add_response = self.client.post(
            reverse("enrollment_add_subject", args=[enrollment.pk]), {"offering": self.off101.pk}, follow=True)
        self.assertRedirects(add_response, reverse("enrollment_detail", args=[enrollment.pk]) + "?subject_added=regular")
        self.assertContains(add_response, "Subject added to this enrollment.")
        self.assertTrue(enrollment.subjects.filter(offering=self.off101).exists())
        self.client.post(reverse("enrollment_action", args=[enrollment.pk]), {
            "action": "discount", "fee": self.scholar.pk})

        detail = self.client.get(reverse("enrollment_detail", args=[enrollment.pk]))
        self.assertEqual(detail.context["assessed"], D("-300.00"))
        self.assertEqual(detail.context["balance"], D("-300.00"))
        self.assertContains(detail, "₱-300.00")
        self.client.post(reverse("enrollment_action", args=[enrollment.pk]), {"action": "approve"})
        detail = self.client.get(reverse("enrollment_detail", args=[enrollment.pk]))
        self.assertEqual(detail.context["assessed"], D("2200.00"))
        self.assertEqual(detail.context["balance"], D("2200.00"))
        self.assertContains(detail, "₱2200.00")

    def test_registrar_can_late_add_to_enrolled_student_and_student_cannot(self):
        enrollment = Enrollment.objects.create(student=self.s1, term=self.term)
        services.add_subject(enrollment, self.off101)
        services.apply_discount(enrollment, self.scholar, self.admin)
        services.change_status(enrollment, "APPROVED", self.registrar)
        Payment.objects.create(enrollment=enrollment, amount=D("1100"), method="CASH", recorded_by=self.cashier)
        services.change_status(enrollment, "ENROLLED", self.registrar)
        self.login(self.registrar)

        detail = self.client.get(reverse("enrollment_detail", args=[enrollment.pk]))
        self.assertContains(detail, "Late add a class")
        self.assertContains(detail, "Adding a class recalculates the assessment and balance.")
        self.assertNotContains(detail, "Drop</button>")
        add_response = self.client.post(
            reverse("enrollment_add_subject", args=[enrollment.pk]), {"offering": self.off102.pk}, follow=True)
        self.assertRedirects(
            add_response, reverse("enrollment_detail", args=[enrollment.pk]) + "?subject_added=late")
        self.assertContains(add_response, "Subject added. Assessment and balance recalculated")
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.status, "ENROLLED")
        detail = self.client.get(reverse("enrollment_detail", args=[enrollment.pk]))
        self.assertEqual(detail.context["assessed"], D("3700.00"))
        self.assertEqual(detail.context["paid"], D("1100"))
        self.assertEqual(detail.context["balance"], D("2600.00"))

        self.login(self.s1.user)
        student_detail = self.client.get(reverse("enrollment_detail", args=[enrollment.pk]))
        self.assertContains(student_detail, "contact the Registrar")
        response = self.client.post(
            reverse("enrollment_add_subject", args=[enrollment.pk]), {"offering": self.off101.pk})
        self.assertEqual(response.status_code, 403)

    def test_rejected_class_add_shows_the_validation_reason(self):
        enrollment = Enrollment.objects.create(student=self.s1, term=self.term)
        self.login(self.registrar)

        response = self.client.post(
            reverse("enrollment_add_subject", args=[enrollment.pk]), {"offering": self.off201.pk}, follow=True)

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Subject was not added.")
        self.assertContains(response, "Missing prerequisite(s): IT101.")


class PasswordTests(BaseData):
    def test_temp_password_is_surname_and_forced_change(self):
        s = Student.objects.create(first_name="Cy", last_name="Dela Rosa", program=self.program)
        created, msg, pw = create_student_portal_account(s)
        self.assertTrue(created)
        self.assertEqual(pw, "Dela Rosa")
        self.assertIn(pw, msg)
        self.assertTrue(s.user.profile.must_change_password)
        # sign in with temporary password -> everything redirects to the change-password page
        self.assertTrue(self.client.post(reverse("login"), {"username": s.student_id, "password": pw}).status_code in (302,))
        r = self.client.get(reverse("student_list"))
        self.assertRedirects(r, reverse("password_change"))
        # same password is refused; a new one clears the flag
        r = self.client.post(reverse("password_change"), {"old_password": pw, "new_password1": pw, "new_password2": pw})
        self.assertEqual(r.status_code, 200)
        new = "Brand-new-Pass-9981"
        r = self.client.post(reverse("password_change"), {"old_password": pw, "new_password1": new, "new_password2": new})
        self.assertRedirects(r, reverse("password_change_done"))
        s.user.profile.refresh_from_db()
        self.assertFalse(s.user.profile.must_change_password)
        self.assertEqual(self.client.get(reverse("student_list")).status_code, 200)

    def test_register_student_is_atomic_and_creates_account(self):
        self.login(self.registrar)
        r = self.client.post(reverse("student_create"), {"first_name": "Di", "last_name": "Lopez", "program": self.program.pk, "year_level": 1})
        st = Student.objects.get(last_name="Lopez")
        self.assertRedirects(r, reverse("student_detail", args=[st.pk]))
        self.assertIsNotNone(st.user)

    def test_login_throttling(self):
        for _ in range(5):
            self.client.post(reverse("login"), {"username": self.s1.student_id, "password": "wrong"})
        r = self.client.post(reverse("login"), {"username": self.s1.student_id, "password": "wrong"})
        self.assertEqual(r.status_code, 429)      # django-axes lockout response
        self.assertContains(r, "Too many failed attempts", status_code=429)


# ---------------------------------------------------------------- permissions
class PermissionTests(BaseData):
    def setUp(self):
        self.e1 = Enrollment.objects.create(student=self.s1, term=self.term, status="APPROVED")
        AssessmentItem.objects.create(enrollment=self.e1, fee=self.misc, description="Misc", amount=D("1000"))

    def test_student_cannot_record_payment(self):
        self.login(self.s1.user)
        url = reverse("record_payment")
        self.assertEqual(self.client.get(url).status_code, 403)
        r = self.client.post(url, {"enrollment": self.e1.pk, "amount": "1000", "method": "CASH"})
        self.assertEqual(r.status_code, 403)
        self.assertEqual(Payment.objects.count(), 0)

    def test_registrar_cannot_record_payment_but_cashier_can(self):
        self.login(self.registrar)
        self.assertEqual(self.client.get(reverse("record_payment")).status_code, 403)  # registrar has no billing module
        self.login(self.cashier)
        r = self.client.post(reverse("record_payment"), {"enrollment": self.e1.pk, "amount": "400", "method": "CASH"})
        p = Payment.objects.get()
        self.assertRedirects(r, reverse("payment_receipt", args=[p.pk]))
        self.assertTrue(p.receipt_no.startswith("OR-"))

    def test_dashboard_is_role_specific(self):
        self.login(self.s1.user)
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, "Student Dashboard")
        self.assertContains(r, self.s1.student_id)
        self.assertContains(r, f'<a href="{reverse("student_detail", args=[self.s1.pk])}">My Record</a>', html=True)
        self.assertNotContains(r, f'href="{reverse("student_list")}"')
        self.assertContains(r, "Your Student Record")
        self.assertContains(r, "View full record and enrollment history")
        self.assertNotContains(r, "Register student")
        self.assertNotContains(r, "Payments recorded")
        self.assertNotContains(r, "Pending review")
        self.assertNotContains(r, "Academic Setup")
        self.assertNotContains(self.client.get(reverse("student_list")), "+ Register student")
        self.login(self.admin)
        r = self.client.get(reverse("dashboard"))
        cards = dict(r.context["cards"])
        self.assertEqual(cards["Students"], Student.objects.count())
        self.assertEqual(cards["Applications"], Enrollment.objects.count())
        self.assertEqual(cards["Pending review"], Enrollment.objects.filter(status="PENDING").count())
        self.assertContains(r, "<small>Students</small>", html=True)
        self.assertContains(r, f"<strong>{cards['Students']}</strong>", html=True)
        self.assertContains(r, "<small>Applications</small>", html=True)
        self.assertContains(r, f"<strong>{cards['Applications']}</strong>", html=True)
        self.assertContains(r, "<small>Pending review</small>", html=True)
        self.assertContains(r, f"<strong>{cards['Pending review']}</strong>", html=True)
        self.assertContains(r, "<small>Payments recorded</small>", html=True)
        self.assertContains(r, f"<strong>{cards['Payments recorded']}</strong>", html=True)
        self.login(self.cashier)
        r = self.client.get(reverse("dashboard"))
        self.assertContains(r, "Payments recorded")
        self.assertNotContains(r, "Pending review")

    def test_navigation_hides_modules(self):
        self.login(self.s1.user)
        r = self.client.get(reverse("dashboard"))
        self.assertNotContains(r, "Academic Setup")
        self.assertNotContains(r, ">Reports<")
        self.login(self.admin)
        self.assertContains(self.client.get(reverse("dashboard")), "Audit Log")

    def test_student_cannot_view_other_students_enrollment_or_billing(self):
        e2 = Enrollment.objects.create(student=self.s2, term=self.old_term)
        self.login(self.s1.user)
        for name in ("enrollment_detail", "billing_detail"):
            self.assertEqual(self.client.get(reverse(name, args=[e2.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("enrollment_detail", args=[self.e1.pk])).status_code, 200)
        rows = self.client.get(reverse("billing_list")).context["rows"]
        self.assertEqual([r["enrollment"] for r in rows], [self.e1])

    def test_student_cannot_process_enrollment_or_edit_records(self):
        self.login(self.s1.user)
        self.assertEqual(self.client.post(reverse("enrollment_action", args=[self.e1.pk]), {"action": "enroll"}).status_code, 403)
        self.assertEqual(self.client.post(reverse("managed_delete", args=["rooms", self.room1.pk])).status_code, 403)
        self.assertEqual(self.client.get(reverse("academic_overview")).status_code, 403)
        self.assertEqual(self.client.get(reverse("audit_log")).status_code, 403)

    def test_student_group_overrides_accidental_staff_groups(self):
        self.s1.user.groups.add(Group.objects.get(name="Registrar"), Group.objects.get(name="Cashier"))
        self.login(self.s1.user)

        dashboard = self.client.get(reverse("dashboard"))
        self.assertContains(dashboard, "Student Dashboard")
        self.assertNotContains(dashboard, "Payments recorded")
        self.assertNotContains(dashboard, "Academic Setup")
        self.assertEqual(self.client.get(reverse("academic_overview")).status_code, 403)
        self.assertEqual(self.client.get(reverse("record_payment")).status_code, 403)

    def test_audit_log_only_for_administrator(self):
        self.login(self.registrar)
        self.assertEqual(self.client.get(reverse("audit_log")).status_code, 403)
        self.login(self.admin)
        self.assertEqual(self.client.get(reverse("audit_log")).status_code, 200)

    def test_orphan_student_account_is_handled(self):
        orphan = mk_user("orphan", "Student")
        self.login(orphan)
        r = self.client.get(reverse("enrollment_create"))
        self.assertRedirects(r, reverse("dashboard"))
        self.assertEqual(self.client.get(reverse("dashboard")).status_code, 200)

    def test_student_cannot_see_enrollment_of_others_in_generic_lists(self):
        Enrollment.objects.create(student=self.s2, term=self.old_term)
        self.login(self.s1.user)
        r = self.client.get(reverse("managed_list", args=["enrollment-records"]))
        self.assertEqual([row["obj"] for row in r.context["rows"]], [self.e1])


# ---------------------------------------------------------------- model rules
class ScheduleTests(BaseData):
    def test_overlapping_room_instructor_and_section_are_rejected(self):
        def clash(offering, room, start, end):
            c = ClassSchedule(offering=offering, day="0", start_time=start, end_time=end, room=room)
            with self.assertRaises(ValidationError):
                c.full_clean()
        clash(self.off102, self.room1, time(9), time(11))    # same room as off101 Monday 8-10
        clash(self.off102, self.room2, time(9), time(11))    # same section (IT-1A) as off101
        ok = ClassSchedule(offering=self.off102, day="0", start_time=time(10), end_time=time(12), room=self.room2)
        ok.full_clean()                                        # touching boundary is fine

    def test_end_before_start_rejected_and_missing_fields_do_not_crash(self):
        with self.assertRaises(ValidationError):
            ClassSchedule(offering=self.off102, day="2", start_time=time(10), end_time=time(9), room=self.room2).full_clean()
        # a half-filled form must produce field errors, not a 500
        self.login(self.admin)
        r = self.client.post(reverse("managed_create", args=["schedules"]), {"day": "0", "start_time": "08:00", "end_time": "09:00"})
        self.assertEqual(r.status_code, 200)


class GradeModelTests(BaseData):
    def test_final_grade_remarks_and_validation(self):
        es = EnrollmentSubject.objects.create(enrollment=Enrollment.objects.create(student=self.s1, term=self.term), offering=self.off101)
        g = Grade.objects.create(enrollment_subject=es, prelim=D("1.50"), midterm=D("2.00"), final=D("2.50"))
        self.assertEqual(g.final_grade, D("2.00"))
        self.assertEqual(g.remarks, "PASSED")
        g.final = D("5.00"); g.midterm = D("5.00"); g.save()
        self.assertEqual(g.remarks, "FAILED")
        g.prelim = D("0.50")
        with self.assertRaises(ValidationError):
            g.full_clean()
        g.prelim = D("9.99")
        with self.assertRaises(ValidationError):
            g.full_clean()


# ---------------------------------------------------------------- enrollment service
class EnrollmentServiceTests(BaseData):
    def setUp(self):
        self.e = Enrollment.objects.create(student=self.s1, term=self.term)

    def assertInvalid(self, offering, fragment, enrollment=None):
        with self.assertRaises(ValidationError) as cm:
            services.add_subject(enrollment or self.e, offering)
        self.assertIn(fragment, " ".join(cm.exception.messages))

    def test_prerequisite_enforced_until_passed(self):
        self.assertInvalid(self.off201, "Missing prerequisite")
        prev = Enrollment.objects.create(student=self.s1, term=self.old_term, status="ENROLLED")
        past_line = EnrollmentSubject.objects.create(enrollment=prev, offering=self.off101)
        Grade.objects.create(enrollment_subject=past_line, prelim=D("4"), midterm=D("4"), final=D("4"))   # failed
        self.assertInvalid(self.off201, "Missing prerequisite")
        Grade.objects.filter(enrollment_subject=past_line).update(prelim=D("1.5"), midterm=D("1.5"), final=D("1.5"))
        services.add_subject(self.e, self.off201)
        self.assertEqual(self.e.subjects.count(), 1)

    def test_capacity_enforced_and_rejected_applications_free_seats(self):
        other = Student.objects.create(first_name="X", last_name="One", program=self.program)
        third = Student.objects.create(first_name="Y", last_name="Two", program=self.program)
        e_other = Enrollment.objects.create(student=other, term=self.term)
        e_third = Enrollment.objects.create(student=third, term=self.term)
        services.add_subject(self.e, self.off101)
        services.add_subject(e_other, self.off101)          # capacity is 2 -> now full
        self.assertInvalid(self.off101, "is full", enrollment=e_third)
        Enrollment.objects.filter(pk=e_other.pk).update(status="REJECTED")
        services.add_subject(e_third, self.off101)

    def test_schedule_clash_and_duplicates(self):
        ClassSchedule.objects.create(offering=self.off102, day="0", start_time=time(10), end_time=time(12), room=self.room2)
        services.add_subject(self.e, self.off101)
        self.assertInvalid(self.off101, "already in this enrollment")
        # off102 does not clash (10-12 vs 8-10); create a clashing offering for same student
        other_section = Section.objects.create(code="IT-1B", term=self.term, program=self.program, year_level=1)
        clashing = SubjectOffering.objects.create(curriculum_subject=self.cur102, section=other_section, instructor=self.other_instructor)
        ClassSchedule.objects.create(offering=clashing, day="0", start_time=time(9), end_time=time(11), room=self.room2)
        self.assertInvalid(clashing, "Schedule conflict")

    def test_subject_from_other_term_rejected_and_locked_after_pending(self):
        sec_old = Section.objects.create(code="OLD", term=self.old_term, program=self.program, year_level=1)
        off_old = SubjectOffering.objects.create(curriculum_subject=self.cur101, section=sec_old, instructor=self.dept_instructor)
        self.assertInvalid(off_old, "not offered in the enrollment's term")
        services.add_subject(self.e, self.off101)
        Enrollment.objects.filter(pk=self.e.pk).update(status="APPROVED")
        self.assertInvalid(self.off102, "only be changed while")

    def test_late_add_recalculates_assessment_and_preserves_payments_and_discounts(self):
        services.add_subject(self.e, self.off101)
        services.apply_discount(self.e, self.scholar, self.admin)
        services.change_status(self.e, "APPROVED", self.registrar)
        Payment.objects.create(enrollment=self.e, amount=D("1100"), method="CASH", recorded_by=self.cashier)
        services.change_status(self.e, "ENROLLED", self.registrar)

        with self.assertRaises(ValidationError):
            services.add_subject(self.e, self.off102)
        services.add_subject(self.e, self.off102, allow_enrolled=True)

        self.e.refresh_from_db()
        self.assertEqual(self.e.status, "ENROLLED")
        self.assertEqual(services.assessed_total(self.e), D("3700.00"))
        self.assertEqual(services.paid_total(self.e), D("1100"))
        self.assertEqual(services.balance(self.e), D("2600.00"))
        self.assertEqual(self.e.assessment_items.filter(fee=self.scholar).get().amount, D("-300"))

    def test_assessment_formula_units_times_rate_plus_fixed(self):
        services.add_subject(self.e, self.off101)
        services.add_subject(self.e, self.off102)
        services.generate_assessment(self.e)
        self.assertEqual(services.total_units(self.e), D("6.0"))
        self.assertEqual(services.assessed_total(self.e), D("6") * D("500") + D("1000"))
        services.generate_assessment(self.e)                  # idempotent: no duplicates
        self.assertEqual(self.e.assessment_items.count(), 2)

    def test_discount_is_kept_on_regeneration_and_reduces_total(self):
        services.add_subject(self.e, self.off101)
        services.apply_discount(self.e, self.scholar, self.admin)
        services.generate_assessment(self.e)
        self.assertEqual(services.assessed_total(self.e), D("3") * D("500") + D("1000") - D("300"))
        with self.assertRaises(ValidationError):
            services.apply_discount(self.e, self.scholar, self.admin)   # no double discount
        with self.assertRaises(ValidationError):
            services.apply_discount(self.e, self.misc, self.admin)       # not a discount fee

    def test_status_workflow(self):
        with self.assertRaises(ValidationError):
            services.change_status(self.e, "APPROVED", self.registrar)    # no subjects yet
        with self.assertRaises(ValidationError):
            services.change_status(self.e, "ENROLLED", self.registrar)    # cannot skip approval
        services.add_subject(self.e, self.off101)
        services.change_status(self.e, "APPROVED", self.registrar)
        self.e.refresh_from_db()
        self.assertEqual((self.e.status, self.e.approved_by), ("APPROVED", self.registrar))
        self.assertIsNotNone(self.e.approved_at)
        self.assertEqual(services.assessed_total(self.e), D("2500.00"))     # generated on approval
        self.assertEqual(services.required_payment(self.e), D("1250.00"))   # 50% default
        with self.assertRaises(ValidationError):
            services.change_status(self.e, "ENROLLED", self.registrar)      # not paid
        Payment.objects.create(enrollment=self.e, amount=D("1000"), method="CASH", recorded_by=self.cashier)
        with self.assertRaises(ValidationError):
            services.change_status(self.e, "ENROLLED", self.registrar)      # not enough
        Payment.objects.create(enrollment=self.e, amount=D("250"), method="CASH", recorded_by=self.cashier)
        services.change_status(self.e, "ENROLLED", self.registrar)
        self.e.refresh_from_db()
        self.assertEqual(self.e.status, "ENROLLED")
        with self.assertRaises(ValidationError):
            services.change_status(self.e, "REJECTED", self.registrar)      # enrolled is final
        self.assertTrue(AuditLog.objects.filter(action="Enrollment set to Enrolled").exists())

    def test_enrollment_view_actions_and_student_cart(self):
        self.login(self.s1.user)
        url = reverse("enrollment_add_subject", args=[self.e.pk])
        self.client.post(url, {"offering": self.off101.pk})
        r = self.client.post(url, {"offering": self.off201.pk}, follow=True)      # prerequisite missing
        self.assertContains(r, "Missing prerequisite")
        self.assertEqual(self.e.subjects.count(), 1)
        # student may not approve
        self.assertEqual(self.client.post(reverse("enrollment_action", args=[self.e.pk]), {"action": "approve"}).status_code, 403)
        # student A cannot touch student B's cart
        eb = Enrollment.objects.create(student=self.s2, term=self.old_term)
        self.assertEqual(self.client.post(reverse("enrollment_add_subject", args=[eb.pk]), {"offering": self.off101.pk}).status_code, 403)
        # registrar approves through the view
        self.login(self.registrar)
        self.client.post(reverse("enrollment_action", args=[self.e.pk]), {"action": "approve"})
        self.e.refresh_from_db()
        self.assertEqual(self.e.status, "APPROVED")
        # after approval the student can no longer edit the cart
        self.login(self.s1.user)
        self.client.post(url, {"offering": self.off102.pk})
        self.assertEqual(self.e.subjects.count(), 1)

    def test_enrollment_form_only_offers_open_terms_and_blocks_duplicates(self):
        self.login(self.s2.user)
        r = self.client.get(reverse("enrollment_create"))
        self.assertEqual(list(r.context["form"].fields["term"].queryset), [self.term])
        self.assertEqual(list(r.context["form"].fields["student"].queryset), [self.s2])
        r = self.client.post(reverse("enrollment_create"), {"student": self.s2.pk, "term": self.old_term.pk, "enrollment_type": "REGULAR"})
        self.assertEqual(r.status_code, 200)                       # closed term refused
        Enrollment.objects.create(student=self.s2, term=self.term)
        r = self.client.post(reverse("enrollment_create"), {"student": self.s2.pk, "term": self.term.pk, "enrollment_type": "REGULAR"})
        self.assertEqual(r.status_code, 200)                       # duplicate -> form error, not 500

    def test_status_cannot_be_edited_through_generic_form(self):
        self.login(self.registrar)
        r = self.client.get(reverse("managed_edit", args=["enrollment-records", self.e.pk]))
        self.assertNotIn("status", r.context["form"].fields)


# ---------------------------------------------------------------- billing
class BillingTests(BaseData):
    def setUp(self):
        self.e = Enrollment.objects.create(student=self.s1, term=self.term, status="APPROVED")
        for desc, amt in (("Tuition", "1500"), ("Misc", "1000"), ("Lab", "500")):
            AssessmentItem.objects.create(enrollment=self.e, fee=self.misc, description=desc, amount=D(amt))
        self.login(self.cashier)

    def pay(self, amount, method="CASH", ref=""):
        return self.client.post(reverse("record_payment"), {"enrollment": self.e.pk, "amount": amount, "method": method, "reference_no": ref})

    def test_balance_not_inflated_by_multiple_items_and_payments(self):
        self.pay("1000"); self.pay("500")
        row = self.client.get(reverse("billing_list")).context["rows"][0]
        self.assertEqual((row["assessed"], row["paid"], row["balance"]), (D("3000"), D("1500"), D("1500")))
        self.assertEqual(services.balance(self.e), D("1500"))

    def test_overpayment_and_missing_reference_rejected(self):
        self.assertEqual(self.pay("3000.01").status_code, 200)
        self.assertEqual(self.pay("100", method="GCASH").status_code, 200)   # no reference number
        self.assertEqual(Payment.objects.count(), 0)
        self.pay("100", method="GCASH", ref="GC-123")
        self.assertEqual(Payment.objects.count(), 1)

    def test_payments_only_for_approved_or_enrolled(self):
        pending = Enrollment.objects.create(student=self.s2, term=self.old_term)
        r = self.client.post(reverse("record_payment"), {"enrollment": pending.pk, "amount": "10", "method": "CASH"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Payment.objects.count(), 0)

    def test_void_restores_balance_and_is_audited(self):
        self.pay("1000")
        p = Payment.objects.get()
        self.assertEqual(self.client.post(reverse("payment_void", args=[p.pk]), {"reason": ""}, follow=True).status_code, 200)
        p.refresh_from_db(); self.assertFalse(p.is_void)                        # reason required
        self.client.post(reverse("payment_void", args=[p.pk]), {"reason": "Wrong student"})
        p.refresh_from_db()
        self.assertTrue(p.is_void)
        self.assertEqual(services.balance(self.e), D("3000"))
        self.assertTrue(AuditLog.objects.filter(action="Voided payment").exists())
        # students and registrars cannot void
        self.login(self.s1.user)
        self.assertEqual(self.client.post(reverse("payment_void", args=[p.pk]), {"reason": "x"}).status_code, 403)

    def test_receipt_visible_to_owner_only(self):
        self.pay("100")
        p = Payment.objects.get()
        self.login(self.s1.user)
        self.assertEqual(self.client.get(reverse("payment_receipt", args=[p.pk])).status_code, 200)
        self.login(self.s2.user)
        self.assertEqual(self.client.get(reverse("payment_receipt", args=[p.pk])).status_code, 403)

    def test_receipt_numbers_unique(self):
        self.pay("100"); self.pay("100")
        numbers = list(Payment.objects.values_list("receipt_no", flat=True))
        self.assertEqual(len(set(numbers)), 2)


# ---------------------------------------------------------------- grades
class GradeEntryTests(BaseData):
    def setUp(self):
        self.e = Enrollment.objects.create(student=self.s1, term=self.term, status="ENROLLED")
        self.e2 = Enrollment.objects.create(student=self.s2, term=self.term, status="PENDING")   # not enrolled -> no row
        self.line = EnrollmentSubject.objects.create(enrollment=self.e, offering=self.off101)
        EnrollmentSubject.objects.create(enrollment=self.e2, offering=self.off101)
        self.url = reverse("class_grades", args=[self.off101.pk])

    def post(self, grades, extra=None):
        self.client.get(self.url)            # opening the sheet creates the grade rows
        g = Grade.objects.get(enrollment_subject=self.line)
        data = {"form-TOTAL_FORMS": "1", "form-INITIAL_FORMS": "1", "form-MIN_NUM_FORMS": "0", "form-MAX_NUM_FORMS": "1000",
                "form-0-id": g.pk, **{f"form-0-{k}": v for k, v in grades.items()}, **(extra or {})}
        return self.client.post(self.url, data, follow=True)

    def test_only_own_instructor_can_open_sheet(self):
        self.login(self.other_teacher_user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.login(self.s1.user)
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.login(self.teacher_user)
        r = self.client.get(self.url)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(len(r.context["formset"].forms), 1)       # only the officially enrolled student

    def test_save_validate_lock_and_student_visibility(self):
        self.login(self.teacher_user)
        r = self.post({"prelim": "9", "midterm": "1.5", "final": "1.5"})
        self.assertContains(r, "Ensure this value is less than or equal to 5")
        self.post({"prelim": "1.5", "midterm": "2", "final": ""}, {"lock": "1"})
        g = Grade.objects.get(enrollment_subject=self.line)
        self.assertFalse(g.is_locked)                             # incomplete sheet cannot be locked
        self.post({"prelim": "1.5", "midterm": "2", "final": "2.5"}, {"lock": "1"})
        g.refresh_from_db()
        self.assertTrue(g.is_locked); self.assertEqual(g.remarks, "PASSED")
        # locked rows are no longer editable by the instructor
        self.assertEqual(len(self.client.get(self.url).context["formset"].forms), 0)
        # student sees only submitted grades; other student sees nothing
        self.login(self.s1.user)
        self.assertEqual([x.pk for x in self.client.get(reverse("grade_list")).context["page_obj"].object_list], [g.pk])
        self.login(self.s2.user)
        self.assertEqual(list(self.client.get(reverse("grade_list")).context["page_obj"].object_list), [])

    def test_draft_grades_hidden_from_student_and_other_instructor(self):
        self.login(self.teacher_user)
        self.post({"prelim": "1.5", "midterm": "2", "final": "2.5"})
        self.login(self.s1.user)
        self.assertEqual(list(self.client.get(reverse("grade_list")).context["page_obj"].object_list), [])
        self.login(self.other_teacher_user)
        self.assertEqual(list(self.client.get(reverse("grade_list")).context["page_obj"].object_list), [])
        self.login(self.teacher_user)
        self.assertEqual(len(self.client.get(reverse("grade_list")).context["page_obj"].object_list), 1)


# ---------------------------------------------------------------- reports, deletes, commands
class ReportAndMiscTests(BaseData):
    def test_csv_blocks_formula_injection_and_respects_filters(self):
        evil = Student.objects.create(first_name="=HYPERLINK(\"http://x\")", last_name="@cmd", program=self.program)
        Enrollment.objects.create(student=evil, term=self.term)
        Enrollment.objects.create(student=self.s1, term=self.old_term)
        self.login(self.registrar)
        body = self.client.get(reverse("reports"), {"format": "csv", "term": self.term.pk}).content.decode("utf-8-sig")
        self.assertNotIn("\n=", body)
        self.assertIn("'@cmd", body)
        self.assertNotIn("Reyes", body)                            # old-term row filtered out

    def test_xlsx_and_pdf_exports(self):
        Enrollment.objects.create(student=self.s1, term=self.term)
        self.login(self.registrar)
        x = self.client.get(reverse("reports"), {"format": "xlsx"})
        self.assertEqual(x.status_code, 200); self.assertTrue(x.content.startswith(b"PK"))
        p = self.client.get(reverse("reports"), {"format": "pdf"})
        self.assertEqual(p.status_code, 200); self.assertTrue(p.content.startswith(b"%PDF"))

    def test_report_totals_exclude_void_payments(self):
        e = Enrollment.objects.create(student=self.s1, term=self.term, status="APPROVED")
        Payment.objects.create(enrollment=e, amount=D("700"), method="CASH", recorded_by=self.cashier)
        Payment.objects.create(enrollment=e, amount=D("300"), method="CASH", recorded_by=self.cashier, is_void=True)
        self.login(self.registrar)
        self.assertEqual(self.client.get(reverse("reports")).context["collections"], D("700"))

    def test_student_delete_blocked_when_records_exist_else_removes_login(self):
        Enrollment.objects.create(student=self.s1, term=self.term)
        self.login(self.registrar)
        r = self.client.post(reverse("student_delete", args=[self.s1.pk]), follow=True)
        self.assertContains(r, "cannot be deleted")
        self.assertTrue(Student.objects.filter(pk=self.s1.pk).exists())
        uid = self.s2.user_id
        self.client.post(reverse("student_delete", args=[self.s2.pk]))
        self.assertFalse(Student.objects.filter(pk=self.s2.pk).exists())
        self.assertFalse(User.objects.filter(pk=uid).exists())

    def test_managed_delete_protected_record(self):
        self.login(self.admin)
        r = self.client.post(reverse("managed_delete", args=["colleges", self.college.pk]), follow=True)
        self.assertContains(r, "cannot be deleted")

    def test_managed_list_search_and_pagination(self):
        for i in range(20):
            Room.objects.create(code=f"LAB-{i:02d}")
        self.login(self.admin)
        r = self.client.get(reverse("managed_list", args=["rooms"]), {"q": "LAB"})
        self.assertEqual(r.context["page_obj"].paginator.count, 20)
        self.assertEqual(len(r.context["rows"]), 15)
        self.assertContains(r, "LAB-00")
        self.assertContains(r, "Page 1 of 2 (20 records)")
        self.assertContains(r, "q=LAB&amp;page=2")
        for slug in ("colleges", "programs", "terms", "subjects", "curriculum", "instructors", "rooms", "sections", "offerings",
                     "schedules", "fees", "enrollment-records", "grades-records", "assessment-items", "enrollment-subjects"):
            self.assertEqual(self.client.get(reverse("managed_list", args=[slug])).status_code, 200, slug)
            self.assertEqual(self.client.get(reverse("managed_create", args=[slug])).status_code, 200, slug)

    def test_academic_setup_shows_college_count_and_records(self):
        self.login(self.admin)
        overview = self.client.get(reverse("academic_overview"))
        self.assertEqual(overview.context["counts"]["colleges"], Department.objects.count())
        self.assertContains(overview, "<strong>1</strong>", html=True)

        colleges = self.client.get(reverse("managed_list", args=["colleges"]))
        self.assertContains(colleges, "CEAT")
        self.assertContains(colleges, "Engineering")
        self.assertNotContains(colleges, "No records yet.")

    def test_all_main_pages_render_for_admin(self):
        e = Enrollment.objects.create(student=self.s1, term=self.term)
        self.login(self.admin)
        for name, args in [("dashboard", []), ("student_list", []), ("academic_overview", []), ("enrollment_list", []),
                           ("enrollment_detail", [e.pk]), ("billing_list", []), ("billing_detail", [e.pk]), ("schedule_list", []),
                           ("grade_list", []), ("class_grades", [self.off101.pk]), ("reports", []), ("audit_log", []),
                           ("student_detail", [self.s1.pk]), ("student_create", []), ("enrollment_create", [])]:
            self.assertEqual(self.client.get(reverse(name, args=args)).status_code, 200, name)

    def test_setup_roles_command(self):
        Group.objects.all().delete()
        User.objects.filter(is_superuser=True).delete()
        call_command("setup_roles", verbosity=0)
        self.assertEqual(set(Group.objects.values_list("name", flat=True)), set(ROLE_GROUPS))
        su = User.objects.get(is_superuser=True)
        self.assertTrue(su.profile.must_change_password)
        call_command("setup_roles", verbosity=0)                    # idempotent
        self.assertEqual(User.objects.filter(is_superuser=True).count(), 1)

    def test_str_methods_are_readable(self):
        es = EnrollmentSubject.objects.create(enrollment=Enrollment.objects.create(student=self.s1, term=self.term), offering=self.off101)
        g = Grade.objects.create(enrollment_subject=es)
        self.assertNotIn("object (", str(g)); self.assertNotIn("object (", str(es))
        self.assertIn("IT101", str(es))
