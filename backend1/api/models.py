from django.db import models
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.contrib.auth.password_validation import validate_password

# Main User Model
class CustomUser(AbstractUser):
    ROLE_CHOICES = [
        ('student', 'Student'),
        ('faculty', 'Faculty'),
        ('staff', 'Staff'),
        ('admin', 'Admin'),
    ]

    # Email will be the unique login field
    username = models.CharField(max_length=150, blank=True, null=True)
    email = models.EmailField(unique=True)

    profile_picture = models.URLField(blank=True, null=True)
    phone = models.CharField(max_length=15, blank=True, null=True)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default='student')

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']  # Needed for createsuperuser

    def set_password(self, raw_password):
        # Validate password strength before saving
        validate_password(raw_password, user=self)
        super().set_password(raw_password)

    def __str__(self):
        return f"{self.email} ({self.role})"


# Student-specific profile
class StudentProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='student_profile')
    roll_number = models.CharField(max_length=20, unique=True)
    department = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.user.email} - {self.roll_number}"


# Faculty-specific profile
class FacultyProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='faculty_profile')
    department = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.user.email} - {self.department}"


# Staff-specific profile
class StaffProfile(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name='staff_profile')
    department = models.CharField(max_length=100)

    def __str__(self):
        return f"{self.user.email} - {self.department}"


# File Upload Model
class UploadedFile(models.Model):
    user = models.ForeignKey(CustomUser, on_delete=models.CASCADE, related_name='uploaded_files')
    file = models.FileField(upload_to='uploads/')
    filename = models.CharField(max_length=255)
    size = models.PositiveBigIntegerField()
    uploaded_at = models.DateTimeField(auto_now_add=True)
    cdn_url = models.URLField(blank=True, null=True)
    year = models.CharField(max_length=10, blank=True, null=True)  # <-- Added year field


    def __str__(self):
        return f"{self.filename} uploaded by {self.user.email}"