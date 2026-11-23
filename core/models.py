from decimal import Decimal
from django.conf import settings
from django.db import models
from django.core.validators import MinValueValidator, MaxValueValidator
from django.db import transaction

# Philippine-style grading: 1.00 (highest) to 5.00 (failed). 3.00 is the passing mark.
PASSING_GRADE = Decimal("3.00")
GRADE_VALIDATORS = [MinValueValidator(Decimal("1.00")), MaxValueValidator(Decimal("5.00"))]
SEMESTERS = [("1st Semester", "1st Semester"), ("2nd Semester", "2nd Semester"), ("Summer", "Summer")]


class UserProfile(models.Model):
    """Extra per-login settings. Used to force a password change after an initial/temporary password."""
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile")
    must_change_password = models.BooleanField(default=False)
    def __str__(self): return f"Profile of {self.user}"

class Department(models.Model):
    code = models.CharField(max_length=15, unique=True)
    name = models.CharField(max_length=120)
    class Meta:
        verbose_name = "College"
        verbose_name_plural = "Colleges"
    def __str__(self): return self.name

class Program(models.Model):
    department = models.ForeignKey(Department, on_delete=models.PROTECT, related_name="programs", verbose_name="College")
    code = models.CharField(max_length=20, unique=True)
    name = models.CharField(max_length=150)
    years_to_complete = models.PositiveSmallIntegerField(default=4)
    def __str__(self): return f"{self.code} - {self.name}"
    class Meta:
        verbose_name = "Academic Program"
        verbose_name_plural = "Academic Programs"

class Student(models.Model):
    GENDER = [("F","Female"),("M","Male"),("X","Prefer not to say")]
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    student_id = models.CharField(max_length=20, unique=True, blank=True)
    first_name = models.CharField(max_length=80)
    middle_name = models.CharField(max_length=80, blank=True)
    last_name = models.CharField(max_length=80)
    birth_date = models.DateField(null=True, blank=True)
    gender = models.CharField(max_length=1, choices=GENDER, blank=True)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=30, blank=True)
    address = models.TextField(blank=True)
    program = models.ForeignKey(Program, on_delete=models.PROTECT, related_name="students")
    year_level = models.PositiveSmallIntegerField(default=1)
    created_at = models.DateTimeField(auto_now_add=True)
    def save(self, *args, **kwargs):
        if not self.student_id:
            with transaction.atomic():
                super().save(*args, **kwargs)
                self.student_id = f"STU-{self.pk:06d}"
                return super().save(update_fields=["student_id"])
        return super().save(*args, **kwargs)
    def __str__(self): return f"{self.student_id} - {self.last_name}, {self.first_name}"

class SchoolTerm(models.Model):
    name = models.CharField(max_length=30, choices=SEMESTERS)
    school_year = models.CharField(max_length=20)
    starts_on = models.DateField(null=True, blank=True)
    ends_on = models.DateField(null=True, blank=True)
    is_open = models.BooleanField(default=False)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["name","school_year"], name="unique_school_term")]
    def __str__(self): return f"{self.name} AY {self.school_year}"

class Subject(models.Model):
    code = models.CharField(max_length=20, unique=True)
    title = models.CharField(max_length=160)
    units = models.DecimalField(max_digits=4, decimal_places=1, validators=[MinValueValidator(Decimal("0.5"))])
    description = models.TextField(blank=True)
    def __str__(self): return f"{self.code} - {self.title}"

class CurriculumSubject(models.Model):
    program = models.ForeignKey(Program, on_delete=models.CASCADE)
    subject = models.ForeignKey(Subject, on_delete=models.PROTECT)
    year_level = models.PositiveSmallIntegerField()
    semester = models.CharField(max_length=20, choices=SEMESTERS)
    prerequisites = models.ManyToManyField(Subject, blank=True, related_name="required_for")
    class Meta:
        constraints = [models.UniqueConstraint(fields=["program","subject","year_level","semester"], name="unique_curriculum_subject")]
    def __str__(self): return f"{self.program.code}: {self.subject.code}"

class Instructor(models.Model):
    user = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    employee_no = models.CharField(max_length=30, unique=True)
    department = models.ForeignKey(Department, on_delete=models.PROTECT, verbose_name="College")
    def __str__(self): return self.user.get_full_name() or self.user.username

class Room(models.Model):
    code = models.CharField(max_length=30, unique=True)
    capacity = models.PositiveIntegerField(default=40)
    def __str__(self): return self.code

class Section(models.Model):
    code = models.CharField(max_length=30)
    term = models.ForeignKey(SchoolTerm, on_delete=models.PROTECT)
    program = models.ForeignKey(Program, on_delete=models.PROTECT)
    year_level = models.PositiveSmallIntegerField()
    class Meta:
        constraints = [models.UniqueConstraint(fields=["code","term"], name="unique_section_term")]
    def __str__(self): return self.code

class SubjectOffering(models.Model):
    curriculum_subject = models.ForeignKey(CurriculumSubject, on_delete=models.PROTECT)
    section = models.ForeignKey(Section, on_delete=models.PROTECT)
    instructor = models.ForeignKey(Instructor, on_delete=models.PROTECT)
    capacity = models.PositiveIntegerField(default=40)
    def __str__(self): return f"{self.curriculum_subject.subject.code} / {self.section}"
    @property
    def enrolled_count(self):
        """Seats taken: every enrollment line except those on rejected applications."""
        return self.enrollmentsubject_set.exclude(enrollment__status="REJECTED").count()

class ClassSchedule(models.Model):
    DAYS = [(str(i), d) for i,d in enumerate(["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday","Sunday"])]
    offering = models.ForeignKey(SubjectOffering, on_delete=models.CASCADE, related_name="schedules")
    day = models.CharField(max_length=1, choices=DAYS)
    start_time = models.TimeField()
    end_time = models.TimeField()
    room = models.ForeignKey(Room, on_delete=models.PROTECT)
    def clean(self):
        from django.core.exceptions import ValidationError
        from django.db.models import Q
        # Required fields may have failed form validation already; don't crash on missing values.
        if not (self.offering_id and self.room_id and self.start_time and self.end_time and self.day != ""):
            return
        if self.start_time >= self.end_time:
            raise ValidationError("End time must be later than start time.")
        clashes = ClassSchedule.objects.filter(day=self.day, start_time__lt=self.end_time, end_time__gt=self.start_time)
        if self.pk: clashes = clashes.exclude(pk=self.pk)
        clashes = clashes.filter(
            Q(room=self.room) | Q(offering__instructor=self.offering.instructor) | Q(offering__section=self.offering.section)
        )
        if clashes.exists():
            raise ValidationError("Schedule conflict: the room, instructor or section is already booked at that time.")
    def __str__(self): return f"{self.offering} {self.get_day_display()} {self.start_time:%H:%M}-{self.end_time:%H:%M}"

class Enrollment(models.Model):
    STATUS = [("PENDING","Pending"),("APPROVED","Approved"),("ENROLLED","Enrolled"),("REJECTED","Rejected")]
    TYPE = [("FRESHMAN","Freshman"),("REGULAR","Regular"),("IRREGULAR","Irregular"),("LATE","Late"),("TRANSFEREE","Transferee"),("SHIFTY","Shifty"),("RETURNING","Returning")]
    student = models.ForeignKey(Student, on_delete=models.PROTECT, related_name="enrollments")
    term = models.ForeignKey(SchoolTerm, on_delete=models.PROTECT)
    enrollment_type = models.CharField(max_length=15, choices=TYPE, default="REGULAR")
    status = models.CharField(max_length=10, choices=STATUS, default="PENDING")
    submitted_at = models.DateTimeField(auto_now_add=True)
    approved_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["student","term"], name="one_enrollment_per_student_term")]
        indexes = [models.Index(fields=["term","status"], name="enrollment_term_status_idx")]
    def __str__(self): return f"{self.student} / {self.term}"

class EnrollmentSubject(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="subjects")
    offering = models.ForeignKey(SubjectOffering, on_delete=models.PROTECT)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["enrollment","offering"], name="unique_enrollment_offering")]
    def __str__(self): return f"{self.enrollment.student.student_id} → {self.offering}"

class Fee(models.Model):
    name = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0"))])
    is_per_unit = models.BooleanField(default=False)
    active = models.BooleanField(default=True)
    is_discount = models.BooleanField(default=False, help_text="Scholarship/discount. Applied manually to an enrollment as a negative charge; never auto-assessed.")
    def __str__(self): return self.name

class AssessmentItem(models.Model):
    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="assessment_items")
    fee = models.ForeignKey(Fee, on_delete=models.PROTECT)
    description = models.CharField(max_length=150)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    class Meta: ordering = ["id"]
    def __str__(self): return f"{self.description}: {self.amount}"

class Payment(models.Model):
    METHOD = [("CASH","Cash"),("GCASH","GCash"),("MAYA","Maya"),("BANK","Bank Transfer"),("OTHER","Other")]
    enrollment = models.ForeignKey(Enrollment, on_delete=models.PROTECT, related_name="payments")
    amount = models.DecimalField(max_digits=10, decimal_places=2, validators=[MinValueValidator(Decimal("0.01"))])
    method = models.CharField(max_length=10, choices=METHOD)
    reference_no = models.CharField(max_length=100, blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    paid_at = models.DateTimeField(auto_now_add=True)
    receipt_no = models.CharField(max_length=30, unique=True, null=True, blank=True, editable=False)
    is_void = models.BooleanField(default=False)
    voided_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="+")
    voided_at = models.DateTimeField(null=True, blank=True)
    void_reason = models.CharField(max_length=200, blank=True)
    def save(self, *args, **kwargs):
        if self.receipt_no:
            return super().save(*args, **kwargs)
        with transaction.atomic():
            super().save(*args, **kwargs)
            self.receipt_no = f"OR-{self.paid_at.year}-{self.pk:06d}"
            super().save(update_fields=["receipt_no"])
    def __str__(self): return f"{self.receipt_no or 'Payment #%s' % self.pk} - {self.amount}"

class Grade(models.Model):
    enrollment_subject = models.OneToOneField(EnrollmentSubject, on_delete=models.CASCADE)
    prelim = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True, validators=GRADE_VALIDATORS)
    midterm = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True, validators=GRADE_VALIDATORS)
    final = models.DecimalField(max_digits=4, decimal_places=2, null=True, blank=True, validators=GRADE_VALIDATORS)
    remarks = models.CharField(max_length=30, blank=True)
    is_locked = models.BooleanField(default=False, help_text="Submitted grades are locked and visible to the student.")
    @property
    def final_grade(self):
        """Equal-weight average of the three terms; None until all three are encoded."""
        if None in (self.prelim, self.midterm, self.final):
            return None
        return ((self.prelim + self.midterm + self.final) / 3).quantize(Decimal("0.01"))
    @property
    def passed(self):
        fg = self.final_grade
        return fg is not None and fg <= PASSING_GRADE
    def save(self, *args, **kwargs):
        fg = self.final_grade
        if fg is not None:
            self.remarks = "PASSED" if fg <= PASSING_GRADE else "FAILED"
        super().save(*args, **kwargs)
    def __str__(self): return f"Grade: {self.enrollment_subject}"

class AuditLog(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True)
    action = models.CharField(max_length=150)
    object_type = models.CharField(max_length=80, blank=True)
    object_id = models.CharField(max_length=40, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta: ordering = ["-created_at", "-id"]
    def __str__(self): return f"{self.created_at:%Y-%m-%d %H:%M} {self.action}"
