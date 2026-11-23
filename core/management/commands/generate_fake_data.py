import random
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone
from faker import Faker

from core.models import (
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


class Command(BaseCommand):
    help = "Create linked fake records for the enrollment system without deleting existing data."

    def add_arguments(self, parser):
        parser.add_argument("--students", type=int, default=12, help="Number of fake students to create.")
        parser.add_argument("--seed", type=int, default=2026, help="Seed used for repeatable fake values.")

    def handle(self, *args, **options):
        student_count = options["students"]
        if student_count < 1:
            raise CommandError("--students must be at least 1.")

        self.rng = random.Random(options["seed"])
        self.fake = Faker("en_PH")
        self.fake.seed_instance(options["seed"])

        with transaction.atomic():
            groups = self._groups()
            registrar = self._user("fake_registrar", "Registrar", groups["Registrar"])
            cashier = self._user("fake_cashier", "Cashier", groups["Cashier"])
            departments, programs, term, subjects = self._academic_data()
            instructors = self._instructors(departments, groups["Instructor"])
            rooms = self._rooms()
            sections = self._sections(programs, term)
            offerings = self._offerings(programs, subjects, sections, instructors, rooms)
            fees = self._fees()
            students = self._students(student_count, programs, groups["Student"])
            self._enrollments(students, term, offerings, fees, registrar, cashier)

        self.stdout.write(self.style.SUCCESS(
            f"Created fake data for {len(students)} students. Existing database records were kept."
        ))

    def _groups(self):
        groups = {}
        for name in ("Registrar", "Cashier", "Instructor", "Student"):
            groups[name], _ = Group.objects.get_or_create(name=name)
        return groups

    def _user(self, username, role, group, first_name=None, last_name=None, email=None):
        first_name = first_name or self.fake.first_name()
        last_name = last_name or self.fake.last_name()
        user_model = get_user_model()
        user, created = user_model.objects.get_or_create(
            username=username,
            defaults={
                "first_name": first_name,
                "last_name": last_name,
                "email": email or self.fake.email(),
            },
        )
        if created:
            user.set_unusable_password()
            user.save(update_fields=["password"])
        user.groups.add(group)
        UserProfile.objects.get_or_create(
            user=user,
            defaults={"must_change_password": self.fake.boolean()},
        )
        return user

    def _academic_data(self):
        department_specs = [
            ("FAKE-ENG", "College of Engineering"),
            ("FAKE-BUS", "College of Business"),
            ("FAKE-EDU", "College of Education"),
        ]
        departments = [
            Department.objects.get_or_create(
                code=code,
                defaults={"name": name},
            )[0]
            for code, name in department_specs
        ]

        program_specs = [
            ("FAKE-IT", "Bachelor of Science in Information Technology", 0),
            ("FAKE-BUS", "Bachelor of Science in Business Administration", 1),
            ("FAKE-EDU", "Bachelor of Secondary Education", 2),
        ]
        programs = [
            Program.objects.get_or_create(
                code=code,
                defaults={
                    "name": name,
                    "department": departments[department_index],
                    "years_to_complete": 4,
                },
            )[0]
            for code, name, department_index in program_specs
        ]

        year = timezone.localdate().year
        term, _ = SchoolTerm.objects.get_or_create(
            name="1st Semester",
            school_year=f"{year}-{year + 1}",
            defaults={
                "starts_on": date(year, 8, 1),
                "ends_on": date(year, 12, 31),
                "is_open": True,
            },
        )

        subject_specs = [
            ("FAKE101", 1),
            ("FAKE102", 1),
            ("FAKE103", 1),
            ("FAKE201", 2),
            ("FAKE202", 2),
            ("FAKE203", 2),
        ]
        subjects = []
        for code, _ in subject_specs:
            subject, _ = Subject.objects.get_or_create(
                code=code,
                defaults={
                    "title": self.fake.catch_phrase()[:160],
                    "units": Decimal(self.rng.choice(("2.0", "3.0", "4.0"))),
                    "description": self.fake.paragraph(),
                },
            )
            subjects.append(subject)

        for program in programs:
            previous_subject = None
            for subject, (_, year_level) in zip(subjects, subject_specs):
                curriculum_subject, _ = CurriculumSubject.objects.get_or_create(
                    program=program,
                    subject=subject,
                    year_level=year_level,
                    semester="1st Semester",
                )
                if previous_subject is not None:
                    curriculum_subject.prerequisites.add(previous_subject)
                previous_subject = subject

        return departments, programs, term, list(zip(subjects, subject_specs))

    def _instructors(self, departments, instructor_group):
        instructors = []
        for index in range(1, 5):
            first_name = self.fake.first_name()
            last_name = self.fake.last_name()
            user = self._user(
                f"fake_instructor_{index:03d}",
                "Instructor",
                instructor_group,
                first_name=first_name,
                last_name=last_name,
            )
            instructor, _ = Instructor.objects.get_or_create(
                user=user,
                defaults={
                    "employee_no": f"FAKE-EMP-{index:03d}",
                    "department": departments[(index - 1) % len(departments)],
                },
            )
            instructors.append(instructor)
        return instructors

    def _rooms(self):
        rooms = []
        for index in range(1, 5):
            room, _ = Room.objects.get_or_create(
                code=f"FAKE-R{index:02d}",
                defaults={"capacity": self.rng.randint(20, 60)},
            )
            rooms.append(room)
        return rooms

    def _sections(self, programs, term):
        sections = {}
        for program_index, program in enumerate(programs, start=1):
            for year_level in (1, 2):
                section, _ = Section.objects.get_or_create(
                    code=f"FAKE-{program_index:02d}-{year_level}A",
                    term=term,
                    defaults={"program": program, "year_level": year_level},
                )
                sections[(program.pk, year_level)] = section
        return sections

    def _offerings(self, programs, subjects, sections, instructors, rooms):
        offerings = {}
        schedule_slots = [
            (str(day), time(hour, 0))
            for day in range(7)
            for hour in range(8, 16)
        ]
        slot_index = 0

        for program in programs:
            for subject, (_, year_level) in subjects:
                curriculum_subject = CurriculumSubject.objects.get(
                    program=program,
                    subject=subject,
                    year_level=year_level,
                    semester="1st Semester",
                )
                section = sections[(program.pk, year_level)]
                instructor = instructors[slot_index % len(instructors)]
                offering, _ = SubjectOffering.objects.get_or_create(
                    curriculum_subject=curriculum_subject,
                    section=section,
                    defaults={
                        "instructor": instructor,
                        "capacity": self.rng.randint(25, 45),
                    },
                )
                offerings.setdefault((program.pk, year_level), []).append(offering)

                if not offering.schedules.exists():
                    day, start_time = schedule_slots[slot_index]
                    ClassSchedule.objects.create(
                        offering=offering,
                        day=day,
                        start_time=start_time,
                        end_time=(datetime.combine(date.min, start_time) + timedelta(hours=1)).time(),
                        room=rooms[slot_index % len(rooms)],
                    )
                slot_index += 1
        return offerings

    def _fees(self):
        specs = [
            ("Demo tuition", Decimal("650.00"), True, False),
            ("Demo technology fee", Decimal("800.00"), False, False),
            ("Demo library fee", Decimal("250.00"), False, False),
            ("Demo scholarship discount", Decimal("500.00"), False, True),
        ]
        return {
            name: Fee.objects.get_or_create(
                name=name,
                defaults={
                    "amount": amount,
                    "is_per_unit": is_per_unit,
                    "active": True,
                    "is_discount": is_discount,
                },
            )[0]
            for name, amount, is_per_unit, is_discount in specs
        }

    def _students(self, count, programs, student_group):
        students = []
        starting_index = Student.objects.count() + 1
        for index in range(starting_index, starting_index + count):
            first_name = self.fake.first_name()
            last_name = self.fake.last_name()
            program = programs[(index - starting_index) % len(programs)]
            year_level = self.rng.choice((1, 2))
            email = f"fake.student.{index}.{self.fake.user_name()}@example.test"
            student = Student.objects.create(
                first_name=first_name,
                middle_name=self.fake.first_name(),
                last_name=last_name,
                birth_date=self.fake.date_of_birth(minimum_age=17, maximum_age=30),
                gender=self.rng.choice(("F", "M", "X")),
                email=email,
                phone=self.fake.numerify(text="09#########"),
                address=self.fake.address(),
                program=program,
                year_level=year_level,
            )
            user = self._user(
                f"fake_student_{student.pk}",
                "Student",
                student_group,
                first_name=first_name,
                last_name=last_name,
                email=email,
            )
            student.user = user
            student.save(update_fields=["user"])
            students.append(student)
        return students

    def _enrollments(self, students, term, offerings, fees, registrar, cashier):
        statuses = ("PENDING", "APPROVED", "ENROLLED", "REJECTED")
        enrollment_types = tuple(value for value, _ in Enrollment.TYPE)
        tuition = fees["Demo tuition"]
        fixed_fees = (fees["Demo technology fee"], fees["Demo library fee"])
        discount = fees["Demo scholarship discount"]

        for index, student in enumerate(students):
            status = statuses[index % len(statuses)]
            decided_at = timezone.now() - timedelta(days=self.rng.randint(0, 30))
            enrollment = Enrollment.objects.create(
                student=student,
                term=term,
                enrollment_type=enrollment_types[index % len(enrollment_types)],
                status=status,
                approved_by=registrar if status != "PENDING" else None,
                approved_at=decided_at if status != "PENDING" else None,
            )
            selected_offerings = offerings[(student.program_id, student.year_level)]
            for offering in selected_offerings:
                enrollment_subject = EnrollmentSubject.objects.create(
                    enrollment=enrollment,
                    offering=offering,
                )
                grade_values = [
                    Decimal(f"{self.rng.uniform(1, 5):.2f}")
                    for _ in range(3)
                ]
                Grade.objects.create(
                    enrollment_subject=enrollment_subject,
                    prelim=grade_values[0],
                    midterm=grade_values[1],
                    final=grade_values[2],
                    is_locked=self.fake.boolean(),
                )

            tuition_amount = sum(
                (offering.curriculum_subject.subject.units * tuition.amount for offering in selected_offerings),
                Decimal("0.00"),
            )
            total = tuition_amount
            assessment_specs = [(tuition, tuition_amount)]
            for fee in fixed_fees:
                assessment_specs.append((fee, fee.amount))
                total += fee.amount
            if self.fake.boolean():
                assessment_specs.append((discount, -discount.amount))
                total -= discount.amount

            for fee, amount in assessment_specs:
                AssessmentItem.objects.create(
                    enrollment=enrollment,
                    fee=fee,
                    description=f"{fee.name} - {self.fake.month_name()}",
                    amount=amount,
                )

            if status in ("APPROVED", "ENROLLED"):
                is_void = index % 2 == 0
                Payment.objects.create(
                    enrollment=enrollment,
                    amount=(total * (Decimal("0.60") if status == "ENROLLED" else Decimal("0.30"))).quantize(Decimal("0.01")),
                    method=self.rng.choice(tuple(value for value, _ in Payment.METHOD)),
                    reference_no=self.fake.bothify(text="DEMO-????-########"),
                    recorded_by=cashier,
                    is_void=is_void,
                    voided_by=cashier if is_void else None,
                    voided_at=timezone.now() - timedelta(days=self.rng.randint(0, 20)) if is_void else None,
                    void_reason=self.fake.sentence() if is_void else "",
                )

            for model_name, object_id in (("Student", student.pk), ("Enrollment", enrollment.pk)):
                AuditLog.objects.create(
                    user=registrar,
                    action=f"Demo: {self.fake.sentence(nb_words=5)}",
                    object_type=model_name,
                    object_id=str(object_id),
                )
