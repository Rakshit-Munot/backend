from django import forms
from django.contrib.auth.forms import UserCreationForm, UserChangeForm
from .models import CustomUser, StudentProfile, FacultyProfile, StaffProfile, WEEKDAYS, DEPARTMENT_CHOICES
import uuid

# ==========================
# Custom User Creation Form
# ==========================
class CustomUserCreationForm(UserCreationForm):
    role = forms.ChoiceField(choices=CustomUser.ROLE_CHOICES, label="Role")

    # --- Student fields ---
    branch = forms.CharField(required=False, label="Branch")
    year = forms.CharField(required=False, label="Year")
    lab_day = forms.ChoiceField(
        choices=WEEKDAYS,
        widget=forms.RadioSelect,
        required=False,
        label="Lab Day",
    )

    # --- Faculty/Staff fields ---
    department = forms.ChoiceField(
        choices=DEPARTMENT_CHOICES,
        required=False,
        label="Department",
    )
    lab_days = forms.MultipleChoiceField(
        choices=WEEKDAYS,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Lab Days",
    )

    class Meta:
        model = CustomUser
        fields = ("username", "email", "role", "password1", "password2")

    def save(self, commit=True):
      user = super().save(commit=False)
      if commit:
          user.save()
          role = self.cleaned_data.get("role")
          if role == "student":
              # Generate a unique roll number automatically
              roll_number = str(uuid.uuid4())[:10]  # first 10 chars of UUID

              StudentProfile.objects.create(
                  user=user,
                  branch=self.cleaned_data.get("branch", ""),
                  year=self.cleaned_data.get("year", ""),
                  lab_day=self.cleaned_data.get("lab_day", ""),
                  roll_number=roll_number,
              )
          elif role == "faculty":
              FacultyProfile.objects.create(
                  user=user,
                  department=self.cleaned_data.get("department"),
                  lab_days=self.cleaned_data.get("lab_days") or [],
              )
          elif role == "staff":
              StaffProfile.objects.create(
                  user=user,
                  department=self.cleaned_data.get("department"),
                  lab_days=self.cleaned_data.get("lab_days") or [],
              )
      return user

# ==========================
# Custom User Change Form
# ==========================
class CustomUserChangeForm(UserChangeForm):
    # --- Student fields ---
    branch = forms.CharField(required=False, label="Branch")
    year = forms.CharField(required=False, label="Year")
    lab_day = forms.ChoiceField(
        choices=WEEKDAYS,
        widget=forms.RadioSelect,
        required=False,
        label="Lab Day",
    )

    # --- Faculty/Staff fields ---
    department = forms.ChoiceField(
        choices=DEPARTMENT_CHOICES,
        required=False,
        label="Department",
    )
    lab_days = forms.MultipleChoiceField(
        choices=WEEKDAYS,
        widget=forms.CheckboxSelectMultiple,
        required=False,
        label="Lab Days",
    )

    class Meta:
        model = CustomUser
        fields = "__all__"
