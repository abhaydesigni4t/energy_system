from django.contrib import admin
from .models import *
# Register your models here.
admin.site.register(SensorReading)  
admin.site.register(HourlyAggregate)
admin.site.register(User)   
admin.site.register(Company)
admin.site.register(Device)
admin.site.register(BillLogo)
admin.site.register(OTPCode)
admin.site.register(InverterReading)