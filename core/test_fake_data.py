from django.core.management import call_command
from django.test import TestCase

from .models import (
    AssessmentItem,
    AuditLog,
    ClassSchedule,
    CurriculumSubject,
    Department,
    Enrollment,
    EnrollmentSubject,
    Fee,
    Grade,
    Instructor,
    Payment,
    Program,
    Room,
    SchoolTerm,
    Section,
    Student,
    Subject,
    SubjectOffering,
    UserProfile,
)


class GenerateFakeDataCommandTests(TestCase):
    def test_creates_connected_records_across_the_enrollment_models(self):
        call_command("generate_fake_data", students=4, seed=19, verbosity=0)

        for model in (
            Department,
            Program,
            SchoolTerm,
            Subject,
            CurriculumSubject,
            Instructor,
            Room,
            Section,
            SubjectOffering,
            ClassSchedule,
            Student,
            Enrollment,
            EnrollmentSubject,
            Fee,
            AssessmentItem,
            Payment,
            Grade,
            AuditLog,
            UserProfile,
        ):
            with self.subTest(model=model.__name__):
                self.assertGreater(model.objects.count(), 0)

        self.assertEqual(Student.objects.count(), 4)
        self.assertEqual(Enrollment.objects.count(), 4)
        self.assertTrue(Payment.objects.filter(is_void=True, voided_at__isnull=False).exists())
