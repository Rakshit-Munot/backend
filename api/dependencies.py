# # auth_dependencies.py
# from ninja.errors import HttpError

# def get_authenticated_user(request):
#     user = request.user
#     if not user.is_authenticated or not user.is_active:
#         raise HttpError(401, "Authentication required")
#     return user

# def admin_required(request):
#     user = get_authenticated_user(request)
#     if user.role != 'admin' and not user.is_superuser:
#         raise HttpError(403, "Admin access required")
#     return user

# def student_required(request):
#     user = get_authenticated_user(request)
#     if user.role != 'student':
#         raise HttpError(403, "Student access required")
#     return user

# def role_required(*roles):
#     def inner(request):
#         user = get_authenticated_user(request)
#         if user.role not in roles and not user.is_superuser:
#             raise HttpError(403, f"Access restricted to roles: {', '.join(roles)}")
#         return user
#     return inner


from functools import wraps
from ninja.errors import HttpError

# ✅ Base check
def get_authenticated_user(request):
    user = request.user
    if not user.is_authenticated or not user.is_active:
        raise HttpError(401, "Authentication required")
    return user

# ✅ Role-checking decorator
def require_role(*roles):
    def decorator(func):
        @wraps(func)
        def wrapper(request, *args, **kwargs):
            user = get_authenticated_user(request)
            # Allow superuser access to everything
            if user.role not in roles and not user.is_superuser:
                raise HttpError(403, f"Access restricted to roles: {', '.join(roles)}")
            return func(request, *args, **kwargs)
        return wrapper
    return decorator

# ✅ Shorthand decorators
admin_only = require_role("admin")
student_only = require_role("student")
faculty_only = require_role("faculty")
staff_only = require_role("staff")

