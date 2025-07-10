from django.db import models
from django.utils import timezone
from django.conf import settings

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
    
    name = models.CharField(max_length=100,blank=False, null=False)
    serial_number = models.CharField(max_length=100,blank=False, null=False)
    cost = models.DecimalField(max_digits=12, decimal_places=2,blank=False, null=False)
    quantity = models.PositiveIntegerField(blank=False, null=False)
    gst_number = models.CharField(max_length=15,blank=False, null=False)
    buyer_name = models.CharField(max_length=100,blank=False, null=False)
    buyer_email = models.EmailField(blank=False, null=False)
    purchase_date = models.DateTimeField(default=timezone.now)
    bill_number = models.CharField(max_length=50, blank=True, null=True)
    remarks = models.TextField(blank=True, null=True)

    def __str__(self):
        return f"{self.name} | SN: {self.serial_number} | Buyer: {self.buyer_name} | Cost: ₹{self.cost}"

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['category', 'sub_category', 'serial_number', 'bill_number'],
                name='unique_item_group'
            )
        ]
    
class IssueRequest(models.Model):
    item = models.ForeignKey('Item', on_delete=models.CASCADE)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    quantity = models.PositiveIntegerField()
    status = models.CharField(max_length=20, choices=[('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')], default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    remarks = models.TextField(blank=True, null=True)
