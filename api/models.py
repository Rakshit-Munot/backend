from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password
from django.contrib.postgres.fields import ArrayField

# ==========================
# Global Choices
# ==========================
WEEKDAYS = [
    ('monday', 'Monday'),
    ('tuesday', 'Tuesday'),
    ('wednesday', 'Wednesday'),
    ('thursday', 'Thursday'),
    ('friday', 'Friday'),
    ('saturday', 'Saturday'),
    ('sunday', 'Sunday'),
]

DEPARTMENT_CHOICES = [
    ('CSE', 'CSE'),
    ('CCE', 'CCE'),
    ('ECE', 'ECE'),
    ('ME', 'ME'),
    ('PHY', 'PHY'),
    ('HSS', 'HSS'),
]

# ==========================
# Main User Model
# ==========================
class CustomUser(AbstractUser):
    ROLE_CHOICES = [
        ('student', 'Student'),
        ('faculty', 'Faculty'),
        ('staff', 'Staff'),
        ('admin', 'Admin'),
    ]

    username = models.CharField(max_length=150, blank=True, null=True)
    email = models.EmailField(unique=True)
    phone = models.CharField(max_length=15, blank=True, null=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='student')

    # For students
    branch = models.CharField(max_length=100, blank=True, null=True)
    year = models.CharField(max_length=10, blank=True, null=True)
    lab_days = ArrayField(
        models.CharField(max_length=15, choices=WEEKDAYS),
        blank=True,
        default=list
    )

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    def clean(self):
        if self.role == 'student':
            if not self.branch or not self.year:
                raise ValidationError("Students must have both branch and year.")
            if len(self.lab_days) > 1:
                raise ValidationError("Students can only have one lab day assigned.")
        elif self.role in ['faculty', 'staff']:
            if len(self.lab_days) == 0:
                raise ValidationError(f"{self.role.capitalize()} must have at least one lab day assigned.")

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    def set_password(self, raw_password):
        validate_password(raw_password, user=self)
        super().set_password(raw_password)

    def __str__(self):
        return f"{self.email} ({self.role})"

# ==========================
# Student Profile
# ==========================
class StudentProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='student_profile')
    roll_number = models.CharField(max_length=20, unique=True)
    branch = models.CharField(max_length=100)
    year = models.CharField(max_length=10)
    lab_day = models.CharField(max_length=20, choices=WEEKDAYS)

    def __str__(self):
        return f"{self.user.email} - {self.roll_number}"

# ==========================
# Faculty Profile
# ==========================
class FacultyProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='faculty_profile')
    department = models.CharField(max_length=10, choices=DEPARTMENT_CHOICES)
    lab_days = models.JSONField(default=list)

    def __str__(self):
        return f"{self.user.email} - Faculty"

# ==========================
# Staff Profile
# ==========================
class StaffProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='staff_profile')
    department = models.CharField(max_length=10, choices=DEPARTMENT_CHOICES)
    lab_days = models.JSONField(default=list)

    def __str__(self):
        return f"{self.user.email} - Staff"

# ==========================
# File Upload Model
# ==========================
class UploadedFile(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='uploaded_files')
    file = models.FileField(upload_to='uploads/')
    filename = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)
    cdn_url = models.CharField(max_length=500, blank=True, null=True)
    year = models.CharField(max_length=10, blank=True, null=True)

    def __str__(self):
        return f"{self.filename} uploaded by {self.user.email}"

# ==========================
# Bill Model (Cloudinary-backed)
# ==========================
class Bill(models.Model):
    bill_no = models.CharField(max_length=100)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    file_url = models.URLField(max_length=500)
    original_filename = models.CharField(max_length=255, blank=True, null=True)
    public_id = models.CharField(max_length=255, blank=True, null=True)
    resource_type = models.CharField(max_length=50, blank=True, null=True)
    comment = models.TextField(blank=True, null=True)
    uploaded_by = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='bills')
    uploaded_at = models.DateTimeField(auto_now_add=True)
    financial_year = models.CharField(max_length=9, db_index=True, blank=True)  # e.g., 2024-2025

    def save(self, *args, **kwargs):
        # Compute financial year BEFORE saving (auto_now_add fires in save, so we use now())
        from datetime import datetime
        from django.utils import timezone
        # Use current time to compute FY
        now = timezone.now()
        year = now.year
        month = now.month
        if month >= 4:
            self.financial_year = f"{year}-{year+1}"
        else:
            self.financial_year = f"{year-1}-{year}"
        super().save(*args, **kwargs)

    def __str__(self):
        return f"Bill {self.bill_no} ({self.financial_year})"
