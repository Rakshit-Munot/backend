from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, StudentProfile, FacultyProfile, StaffProfile, WEEKDAYS,DEPARTMENT_CHOICES
from .forms import CustomUserCreationForm, CustomUserChangeForm
import uuid


# ------------------------
# Profile Forms
# ------------------------
class StudentProfileForm(forms.ModelForm):
    lab_day = forms.ChoiceField(
        choices=WEEKDAYS,
        widget=forms.RadioSelect,
        label="Lab Day",
    )
    roll_number = forms.CharField(required=True, label="Roll Number")  # <-- Add this
    class Meta:
        model = StudentProfile
        fields = ('branch', 'year',)


class FacultyProfileForm(forms.ModelForm):
    department = forms.ChoiceField(
        choices=DEPARTMENT_CHOICES,
        required=False,
        label="Department"
    )
    lab_days = forms.MultipleChoiceField(
        choices=WEEKDAYS,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Lab Days",
    )

    class Meta:
        model = FacultyProfile
        fields = ('department',)

# ------------------------
# Staff Profile Form
# ------------------------
class StaffProfileForm(forms.ModelForm):
    department = forms.ChoiceField(
        choices=DEPARTMENT_CHOICES,
        required=False,
        label="Department"
    )
    lab_days = forms.MultipleChoiceField(
        choices=WEEKDAYS,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Lab Days",
    )

    class Meta:
        model = StaffProfile
        fields = ('department',)


# ------------------------
# Inlines
# ------------------------
class StudentProfileInline(admin.StackedInline):
    model = StudentProfile
    form = StudentProfileForm
    can_delete = False
    verbose_name_plural = "Student Profile"


class FacultyProfileInline(admin.StackedInline):
    model = FacultyProfile
    form = FacultyProfileForm
    can_delete = False
    verbose_name_plural = "Faculty Profile"


class StaffProfileInline(admin.StackedInline):
    model = StaffProfile
    form = StaffProfileForm
    can_delete = False
    verbose_name_plural = "Staff Profile"


# ------------------------
# Custom User Admin
# ------------------------
@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    form = CustomUserChangeForm
    add_form = CustomUserCreationForm
    model = CustomUser

    list_display = (
        "username",
        "email",
        "role",
        "get_branch",
        "get_year",
        "get_department",
        "get_lab_days",
        "is_active",
        "is_staff",
    )
    list_filter = ("role", "is_staff")
    search_fields = ("username", "email")
    ordering = ("email",)

    fieldsets = (
        (None, {"fields": ("username", "email", "password", "role")}),
        ("Personal info", {"fields": ("first_name", "last_name", "phone")}),
        (
            "Permissions",
            {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")},
        ),
        ("Important dates", {"fields": ("last_login", "date_joined")}),
    )

    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": (
                "username",
                "email",
                "role",
                "password1",
                "password2",
                # The inline fields will be dynamically shown by JS
                "branch",
                "year",
                "lab_day",
                "lab_days",
                "department",
            ),
        }),
    )
    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)

        # Only create profile if adding new user
        if not change:
            role = form.cleaned_data.get("role")
            if role == "student":
                # Generate a unique roll number automatically
                roll_number = str(uuid.uuid4())[:10]  # first 10 chars of UUID

                StudentProfile.objects.create(
                    user=obj,
                    branch=form.cleaned_data.get("branch", ""),
                    year=form.cleaned_data.get("year", ""),
                    lab_day=form.cleaned_data.get("lab_day", ""),
                    roll_number=roll_number,
                )
            elif role == "faculty":
                FacultyProfile.objects.create(
                    user=obj,
                    department=form.cleaned_data.get("department"),
                    lab_days=form.cleaned_data.get("lab_days") or [],
                )
            elif role == "staff":
                StaffProfile.objects.create(
                    user=obj,
                    department=form.cleaned_data.get("department"),
                    lab_days=form.cleaned_data.get("lab_days") or [],
                )

    # ------------------------
    # Display profile info in list
    # ------------------------
    def get_branch(self, obj):
        if obj.role == "student" and hasattr(obj, "student_profile"):
            return obj.student_profile.branch
        return "-"
    get_branch.short_description = "Branch"

    def get_year(self, obj):
        if obj.role == "student" and hasattr(obj, "student_profile"):
            return obj.student_profile.year
        return "-"
    get_year.short_description = "Year"

    def get_lab_days(self, obj):
        if obj.role == "student" and hasattr(obj, "student_profile"):
            return obj.student_profile.lab_day.title()
        elif obj.role == "faculty" and hasattr(obj, "faculty_profile"):
            return ", ".join(day.title() for day in obj.faculty_profile.lab_days)
        elif obj.role == "staff" and hasattr(obj, "staff_profile"):
            return ", ".join(day.title() for day in obj.staff_profile.lab_days)
        return "-"
    get_lab_days.short_description = "Lab Days"

    def get_department(self, obj):
        if obj.role == "faculty" and hasattr(obj, "faculty_profile"):
            dept = obj.faculty_profile.department
            return getattr(dept, "name", str(dept))  # Safe access
        elif obj.role == "staff" and hasattr(obj, "staff_profile"):
            dept = obj.staff_profile.department
            return getattr(dept, "name", str(dept))
        return "-"
    get_department.short_description = "Department"

    # ------------------------
    # Show relevant inline when editing existing user
    # ------------------------
    def get_inlines(self, request, obj=None):
        if obj:
            if obj.role == "student":
                return [StudentProfileInline]
            elif obj.role == "faculty":
                return [FacultyProfileInline]
            elif obj.role == "staff":
                return [StaffProfileInline]
        return []

    # ------------------------
    # Include JS to dynamically show/hide fields on Add User page
    # ------------------------
    class Media:
        js = ("admin/js/custom_user_admin.js",)
