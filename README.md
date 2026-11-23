# Student Information and Enrollment System (Django + MySQL)

Academic project starter implementation for a computerized enrollment system.

## Included modules
- Student registration with generated unique Student ID (`STU-000001` style)
- Program, department, subject, curriculum, prerequisite, term, section, room, instructor, offering and schedule data models
- Enrollment application with type and status tracking
- Schedule conflict validation at model level (room and instructor)
- Assessment item and payment ledger models; balance summary view
- Grade records
- Role/group access helper for Administrator, Registrar, Cashier, Instructor, Student
- Website-based CRUD screens for student records and academic master records, with Django Admin retained for advanced administration
- Enrollment summary, collections total and CSV export for Excel
- Audit log model for important actions

## Requirements
- Python 3.11+
- Django 5.2
- MySQL 8.0+ (or SQLite fallback for local evaluation)
- VS Code recommended

## Student enrollment status
The student list shows **Enrolled** only when the student has an `Enrollment` record with status `ENROLLED` for the selected semester. Otherwise, for that semester, the student is shown as **Unenrolled**. Pending, approved, and rejected records do not count as officially enrolled. Select a semester in the student list to view the correct term-specific status. A student can therefore be enrolled in one semester and unenrolled in another.

## Student portal login
When a Registrar or Administrator registers a student, the system creates a linked portal login automatically:
- **Username:** the generated Student ID (for example, `STU-000123`)
- **Initial password:** the student's last name exactly as entered (passwords are case-sensitive)
- The account is assigned to the `Student` role group.
- Students can sign in at `/accounts/login/` and change their password using **Change Password** in the top navigation.
- For a student record created before this feature, open the student's profile and use **Create Student Login Account** if no account is linked.

## Configure role groups
In Django Admin, create Groups named exactly:
- Administrator
- Registrar
- Cashier
- Instructor
- Student

Assign users to the appropriate group. Create Instructor records linked to instructor users. For a student portal account, create a Student record linked to the student's User account.

## Suggested initial data sequence
1. College (Department)
2. Academic Program
3. School Term (set `is_open=True`)
4. Subjects
5. Curriculum Subject rows (assign prerequisites)
6. Rooms, Instructor profiles, Sections
7. Subject Offerings and Class Schedules
8. Fees
9. Student records and enrollment applications

## Generate fake data
Install the optional Faker dependency and populate linked sample records without deleting existing data:

```powershell
python -m pip install -r requirements-demo.txt
python manage.py generate_fake_data --students 12 --seed 2026
```

The command creates academic records, students and accounts with unusable passwords, schedules, enrollments, assessments, payments, grades and audit logs. Run migrations first. Choose a different `--seed` to vary the generated values; each run adds more student records.

