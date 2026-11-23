from .models import Student
from .permissions import can_manage, can_record_payments, is_student, is_instructor, modules_for


def navigation(request):
    user = request.user
    student_user = is_student(user)
    student_record_id = None
    if student_user:
        student_record_id = Student.objects.filter(user=user).values_list("pk", flat=True).first()
    return {
        "modules": modules_for(user),
        "is_student_user": student_user,
        "student_record_id": student_record_id,
        "is_instructor_user": is_instructor(user),
        "user_can_manage": can_manage(user) if user.is_authenticated else False,
        "user_can_pay": can_record_payments(user) if user.is_authenticated else False,
    }
