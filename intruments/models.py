from django.db import models
from django.utils import timezone
from django.conf import settings
from datetime import timedelta


class Category(models.Model):
    name = models.CharField(max_length=50, unique=True)

    def __str__(self):
        return self.name


class SubCategory(models.Model):
    name = models.CharField(max_length=50)
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='subcategories')

    class Meta:
        unique_together = ('name', 'category')

    def __str__(self):
        return f"{self.category.name} > {self.name}"


class Item(models.Model):
    category = models.ForeignKey(Category, on_delete=models.CASCADE, related_name='items')
    sub_category = models.ForeignKey(SubCategory, on_delete=models.CASCADE, related_name='items', null=True, blank=True)

    name = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    quantity = models.PositiveIntegerField()
    is_consumable = models.BooleanField(default=False)  # False = non-consumable
    location = models.CharField(max_length=120, blank=True)
    is_available = models.BooleanField(default=True, help_text="If false, item is not available for issue/requests")

    # per-issue quantity limits
    min_issue_limit = models.PositiveIntegerField(default=1)
    max_issue_limit = models.PositiveIntegerField(default=1)

    remarks = models.TextField(blank=True, null=True)

    def __str__(self):
        return self.name

    @property
    def available_quantity(self):
        """
        - Non-consumable: available = quantity - approved outstanding (not yet submitted) - pending (reserve pending)
        - Consumable: available = current stock (quantity is decremented on pending request to hard-lock)
        """
        if not self.is_available:
            return 0
        if self.is_consumable:
            return self.quantity
        issued_qty = IssueRequest.objects.filter(item=self, status='approved').exclude(submission_status='submitted').aggregate(
            total=models.Sum('quantity')
        )['total'] or 0
        pending_qty = IssueRequest.objects.filter(item=self, status='pending').aggregate(
            total=models.Sum('quantity')
        )['total'] or 0
        return max(0, self.quantity - issued_qty - pending_qty)


class IssueRequest(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    item = models.ForeignKey(Item, on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    approved_at = models.DateTimeField(blank=True, null=True)
    return_by = models.DateTimeField(blank=True, null=True)
    remarks = models.TextField(blank=True, null=True)

    # Return/submission tracking
    SUBMISSION_CHOICES = [
        ("not_required", "Not Required"),  # for consumables
        ("pending", "Pending"),            # for non-consumables until submitted
        ("submitted", "Submitted"),        # when returned/submitted by student
    ]
    submission_status = models.CharField(max_length=20, choices=SUBMISSION_CHOICES, default="pending")
    submitted_at = models.DateTimeField(blank=True, null=True)

    def approve(self, no_of_days=7):
        if self.status != 'pending':
            raise ValueError("Request is already processed")

        item = self.item
        if item.is_consumable:
            if self.quantity > item.quantity:
                raise ValueError("Not enough stock available")
            item.quantity -= self.quantity
            item.save()
        else:
            if self.quantity > item.available_quantity:
                raise ValueError("Not enough items available to issue")

        self.status = 'approved'
        self.approved_at = timezone.now()
        self.return_by = self.approved_at + timedelta(days=no_of_days)
        self.save()

    def reject(self, reason=""):
        if self.status != 'pending':
            raise ValueError("Request is already processed")
        self.status = 'rejected'
        if reason:
            self.remarks = reason
        self.save()

    def __str__(self):
        return f"{self.user} -> {self.item.name} ({self.quantity}) | Status: {self.status}"


class IssueMessage(models.Model):
    TYPE_CHOICES = [
        ("admin", "Admin"),
        ("system", "System"),
    ]

    issue_request = models.ForeignKey(IssueRequest, on_delete=models.CASCADE, related_name="messages")
    msg_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default="admin")
    text = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    creator = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        who = "system" if self.msg_type == "system" else (getattr(self.creator, "username", None) or "admin")
        return f"[{self.msg_type}] {who}: {self.text[:30]}" 