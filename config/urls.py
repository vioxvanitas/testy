from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path

from core import views

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/login/", auth_views.LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("accounts/logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("accounts/password/change/", views.ForcedPasswordChangeView.as_view(), name="password_change"),
    path("accounts/password/change/done/", auth_views.PasswordChangeDoneView.as_view(
        template_name="registration/password_change_done.html"), name="password_change_done"),
    path("", views.dashboard, name="dashboard"),
    path("students/", views.student_list, name="student_list"),
    path("students/new/", views.student_create, name="student_create"),
    path("students/<int:pk>/", views.student_detail, name="student_detail"),
    path("students/<int:pk>/edit/", views.student_edit, name="student_edit"),
    path("students/<int:pk>/create-account/", views.student_create_portal_account, name="student_create_portal_account"),
    path("students/<int:pk>/delete/", views.student_delete, name="student_delete"),
    path("manage/<slug:slug>/", views.managed_list, name="managed_list"),
    path("manage/<slug:slug>/new/", views.managed_form, name="managed_create"),
    path("manage/<slug:slug>/<int:pk>/edit/", views.managed_form, name="managed_edit"),
    path("manage/<slug:slug>/<int:pk>/delete/", views.managed_delete, name="managed_delete"),
    path("academics/", views.academic_overview, name="academic_overview"),
    path("enrollments/", views.enrollment_list, name="enrollment_list"),
    path("enrollments/new/", views.enrollment_create, name="enrollment_create"),
    path("enrollments/<int:pk>/", views.enrollment_detail, name="enrollment_detail"),
    path("enrollments/<int:pk>/add-subject/", views.enrollment_add_subject, name="enrollment_add_subject"),
    path("enrollments/<int:pk>/drop/<int:line_pk>/", views.enrollment_drop_subject, name="enrollment_drop_subject"),
    path("enrollments/<int:pk>/action/", views.enrollment_action, name="enrollment_action"),
    path("billing/", views.billing_list, name="billing_list"),
    path("billing/payment/", views.record_payment, name="record_payment"),
    path("billing/<int:pk>/", views.billing_detail, name="billing_detail"),
    path("billing/receipt/<int:pk>/", views.payment_receipt, name="payment_receipt"),
    path("billing/payment/<int:pk>/void/", views.payment_void, name="payment_void"),
    path("schedules/", views.schedule_list, name="schedule_list"),
    path("grades/", views.grade_list, name="grade_list"),
    path("grades/class/<int:pk>/", views.class_grades, name="class_grades"),
    path("reports/", views.reports, name="reports"),
    path("audit/", views.audit_log, name="audit_log"),
]
