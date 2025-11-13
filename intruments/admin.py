from django.contrib import admin
from django.utils.html import format_html
from .models import Item, IssueRequest


@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
	list_display = ("id", "name", "category", "sub_category", "quantity", "is_consumable", "is_available")
	list_filter = ("category", "sub_category", "is_consumable", "is_available")
	search_fields = ("name",)
	ordering = ("name",)


@admin.register(IssueRequest)
class IssueRequestAdmin(admin.ModelAdmin):
	"""Admin for Issue Requests with sorting by student name and approval timestamps."""

	def student_name(self, obj):
		user = getattr(obj, "user", None)
		if not user:
			return "-"
		try:
			full = getattr(user, "get_full_name", None)
			if callable(full):
				name = (full() or "").strip()
				if name:
					return name
		except Exception:
			pass
		return getattr(user, "username", None) or getattr(user, "email", None) or "-"

	student_name.short_description = "Student"
	student_name.admin_order_field = "user__username"

	list_display = (
		"id",
		"student_name",
		"item",
		"quantity",
		"status",
		"created_at",
		"approved_at",
		"return_by",
		"submission_status",
		"submitted_at",
	)

	list_filter = ("status", "submission_status", "approved_at", "return_by")
	search_fields = ("user__username", "user__email", "item__name", "remarks")
	# Default ordering: recently approved first, then newest created
	ordering = ("-approved_at", "-created_at")
	date_hierarchy = "created_at"
