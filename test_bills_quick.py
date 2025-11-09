#!/usr/bin/env python
"""Quick test to verify bills are visible via API and have correct FY."""
import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'backend1.settings')
django.setup()

from api.models import Bill
from django.utils import timezone

print("=" * 60)
print("BILL DATABASE CHECK")
print("=" * 60)

bills = Bill.objects.all().order_by('-uploaded_at')
print(f"\nTotal bills in DB: {bills.count()}")

if bills.exists():
    print("\nRecent bills:")
    for b in bills[:5]:
        print(f"  ID: {b.id}")
        print(f"  Bill No: {b.bill_no}")
        print(f"  Amount: {b.amount}")
        print(f"  Financial Year: {b.financial_year}")
        print(f"  Uploaded: {b.uploaded_at}")
        print(f"  URL: {b.file_url[:80]}...")
        print()

    print("\nFinancial Year breakdown:")
    for fy in Bill.objects.values_list('financial_year', flat=True).distinct().order_by('-financial_year'):
        count = Bill.objects.filter(financial_year=fy).count()
        print(f"  {fy}: {count} bills")

    # Current FY
    now = timezone.now()
    year = now.year
    month = now.month
    current_fy = f"{year}-{year+1}" if month >= 4 else f"{year-1}-{year}"
    print(f"\nCurrent Financial Year (computed): {current_fy}")
    current_bills = Bill.objects.filter(financial_year=current_fy).count()
    print(f"Bills in current FY: {current_bills}")
else:
    print("\n⚠ No bills found in database.")

print("\n" + "=" * 60)
print("✅ Check complete. If bills show here, API should serve them.")
print("=" * 60)
