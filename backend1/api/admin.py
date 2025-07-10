from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import CustomUser, StudentProfile, FacultyProfile, StaffProfile

@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    model = CustomUser
    list_display = ('username', 'email', 'role', 'is_active', 'is_staff')  # 👈 username added
    list_filter = ('role', 'is_staff')

    fieldsets = (
        (None, {'fields': ('username', 'email', 'password', 'role')}),  # 👈 username added
        ('Personal info', {'fields': ('first_name', 'last_name', 'phone', 'profile_picture')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )

    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('username', 'email', 'role', 'password1', 'password2'),  # 👈 username added
        }),
    )

    search_fields = ('username', 'email')  # 👈 username added
    ordering = ('email',)


# Register role-specific profile models
admin.site.register(StudentProfile)
admin.site.register(FacultyProfile)
admin.site.register(StaffProfile)



# from django.contrib import admin
# from django.contrib.auth.admin import UserAdmin
# from django.contrib.auth.models import Group
# from django.contrib.auth import get_user_model

# User = get_user_model()

# @admin.register(User)
# class CustomUserAdmin(UserAdmin):
#     def get_model_perms(self, request):
#         # Make this model appear under "Authentication and Authorization"
#         return super().get_model_perms(request)

#     def get_app_label(self):
#         # Force it to appear under the "auth" app in admin
#         return 'auth'
