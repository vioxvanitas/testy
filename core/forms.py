from django import forms

from . import services
from .models import Enrollment, Grade, Payment, SchoolTerm, Student
from .permissions import is_student


class StudentForm(forms.ModelForm):
    class Meta:
        model = Student
        fields = ["first_name","middle_name","last_name","birth_date","gender","email","phone","address","program","year_level"]
        widgets = {"birth_date": forms.DateInput(attrs={"type":"date"})}


class EnrollmentForm(forms.ModelForm):
    class Meta:
        model = Enrollment
        fields = ["student","term","enrollment_type"]

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["term"].queryset = SchoolTerm.objects.filter(is_open=True)   # open terms only
        self.fields["student"].queryset = Student.objects.select_related("program").order_by("student_id")
        if user is not None and is_student(user):
            own = Student.objects.filter(user=user)
            self.fields["student"].queryset = own
            self.fields["student"].empty_label = None


class PaymentForm(forms.ModelForm):
    class Meta:
        model = Payment
        fields = ["enrollment","amount","method","reference_no"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Payments are accepted only after the application has been approved (assessment exists).
        self.fields["enrollment"].queryset = Enrollment.objects.filter(status__in=["APPROVED","ENROLLED"]) \
            .select_related("student","term").order_by("student__student_id")

    def clean(self):
        data = super().clean()
        enrollment, amount, method = data.get("enrollment"), data.get("amount"), data.get("method")
        if enrollment and amount is not None:
            outstanding = services.balance(enrollment)
            if amount > outstanding:
                self.add_error("amount", f"Payment exceeds the outstanding balance of ₱{outstanding:,.2f}.")
        if method and method != "CASH" and not data.get("reference_no"):
            self.add_error("reference_no", "A reference number is required for non-cash payments.")
        return data


class GradeEntryForm(forms.ModelForm):
    class Meta:
        model = Grade
        fields = ["prelim","midterm","final"]
        widgets = {f: forms.NumberInput(attrs={"step":"0.25","min":"1","max":"5","style":"width:90px"}) for f in ["prelim","midterm","final"]}
