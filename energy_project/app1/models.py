from django.db import models
from django.utils import timezone
from datetime import datetime
from django.contrib.auth.models import AbstractUser
from django.db import models


from django.contrib.auth.models import UserManager as BaseUserManager

class CustomUserManager(BaseUserManager):
    def create_superuser(self, username, email=None, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('role', 'superadmin')  # Set role to superadmin
        
        if extra_fields.get('is_staff') is not True:
            raise ValueError('Superuser must have is_staff=True.')
        if extra_fields.get('is_superuser') is not True:
            raise ValueError('Superuser must have is_superuser=True.')
        
        return self._create_user(username, email, password, **extra_fields)

class User(AbstractUser):
    """
    Extended User model with role and unit_id support.
    Roles: 'admin', 'superadmin', 'user'
    unit_ids: list of unit IDs this user can access
    """
    ROLE_CHOICES = [
        ('superadmin', 'Super Admin'),
        ('admin', 'Admin'),
        ('user', 'User'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='user')
    unit_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of unit IDs this user has access to"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    # ── Fix reverse accessor clashes with auth.User ──────────────────────────
    groups = models.ManyToManyField(
        'auth.Group',
        verbose_name='groups',
        blank=True,
        related_name='app1_user_set',
        related_query_name='app1_user',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        verbose_name='user permissions',
        blank=True,
        related_name='app1_user_set',
        related_query_name='app1_user',
    )
    

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def is_admin_or_superadmin(self):
        return self.role in ('admin', 'superadmin')

    def get_unit_ids(self):
        return self.unit_ids if self.unit_ids else []

    def can_access_unit(self, unit_id):
        if self.is_admin_or_superadmin():
            return True
        return int(unit_id) in [int(uid) for uid in self.get_unit_ids()]
    
    def save(self, *args, **kwargs):
        # Auto-set role for superusers
        if self.is_superuser and self.role == 'user':
            self.role = 'superadmin'
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.username} ({self.role})"
    
    
class SensorReading(models.Model):
    """
    Stores each Modbus reading as a separate record for historical data
    """
    unit_id = models.PositiveIntegerField(db_index=True)
    timestamp = models.FloatField(help_text="Unix timestamp")
    block1_start_reg = models.PositiveIntegerField(default=40100)
    block1_raw = models.JSONField(help_text="Raw registers from block 1")
    block2_start_reg = models.PositiveIntegerField(default=40138)
    block2_raw = models.JSONField(help_text="Raw registers from block 2")
    created_at = models.DateTimeField(auto_now_add=True)
    
    # Cached/decoded values for faster queries
    voltage = models.FloatField(null=True, blank=True)
    current = models.FloatField(null=True, blank=True)
    active_power_kw = models.FloatField(null=True, blank=True)
    reactive_power_kvar = models.FloatField(null=True, blank=True)
    apparent_power_kva = models.FloatField(null=True, blank=True)
    energy_today_kwh = models.FloatField(null=True, blank=True)
    
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['unit_id', 'timestamp']),
            models.Index(fields=['-timestamp']),
        ]
    
    def save(self, *args, **kwargs):
        if self.block1_raw and len(self.block1_raw) > 0:
            # Voltages
            v_a = self.block1_raw[0] / 10
            v_b = self.block1_raw[1] / 10
            v_c = self.block1_raw[2] / 10
            self.voltage = round((v_a + v_b + v_c) / 3, 1)
            
            # Currents
            i_a = self.block1_raw[8] / 10
            i_b = self.block1_raw[9] / 10
            i_c = self.block1_raw[10] / 10
            self.current = round((i_a + i_b + i_c) / 3, 1)
            
            # Active Power (from V×I calculation)
            total_watts = (v_a * i_a) + (v_b * i_b) + (v_c * i_c)
            self.active_power_kw = round(total_watts / 1000, 1)
            
            # Reactive Power
            self.reactive_power_kvar = self.block1_raw[21] / 10 if len(self.block1_raw) > 21 else 0
            
            # Apparent Power
            self.apparent_power_kva = self.block1_raw[22] / 10 if len(self.block1_raw) > 22 else 0
        
        if self.block2_raw and len(self.block2_raw) > 1:
            # Combine two 16‑bit registers (low word first, high word second)
            low = self.block2_raw[0]
            high = self.block2_raw[1]
            raw_32 = (high << 16) | low          # same as high*65536 + low
            
            # ─────────────────────────────────────────────────────────────
            # ADJUST THIS SCALE FACTOR TO MATCH YOUR METER'S DOCUMENTATION
            # Common values: 100 (0.01 kWh units), 1000 (0.001 kWh), etc.
            # Start with 100, then change until displayed total matches the meter.
            SCALE = 100
            # ─────────────────────────────────────────────────────────────
            
            # Store the TOTAL cumulative energy (never resets) with full precision
            self.energy_today_kwh = raw_32 / SCALE   # no rounding here
            
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"Unit {self.unit_id} @ {datetime.fromtimestamp(self.timestamp)}"

class HourlyAggregate(models.Model):
    """
    Pre-calculated hourly averages for graph performance
    """
    unit_id = models.PositiveIntegerField(db_index=True)
    hour = models.DateTimeField(db_index=True)
    avg_active_power_kw = models.FloatField(default=0)
    min_active_power_kw = models.FloatField(default=0)
    max_active_power_kw = models.FloatField(default=0)
    total_energy_kwh = models.FloatField(default=0)
    reading_count = models.PositiveIntegerField(default=0)
    
    class Meta:
        unique_together = [['unit_id', 'hour']]
        ordering = ['-hour']
        
        
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.core.cache import cache

@receiver(post_save, sender=SensorReading)
def clear_graph_cache(sender, instance, **kwargs):
    """Clear cached graph data when new reading is added"""
    # Clear cache for different time periods
    cache_keys = [
        f'graph_data_unit_{instance.unit_id}_today',
        f'graph_data_unit_{instance.unit_id}_week',
        f'graph_data_unit_{instance.unit_id}_month',
    ]
    for key in cache_keys:
        cache.delete(key)
        
        

class Company(models.Model):
    """
    Represents a client company that owns devices/units.
    """
    name = models.CharField(max_length=200)
    location = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        'User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_companies',
    )

    class Meta:
        verbose_name = 'Company'
        verbose_name_plural = 'Companies'
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_device_count(self):
        return self.devices.count()


class Device(models.Model):
    """
    Represents a physical Modbus unit (energy meter / sensor).
    unit_id must match what the SBC sends in readings.
    """
    unit_id = models.PositiveIntegerField(unique=True, db_index=True)
    name = models.CharField(max_length=200, default='', blank=True)
    description = models.TextField(blank=True)
    location = models.CharField(max_length=300, blank=True)

    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='devices',
    )
    # Which users can access this device
    assigned_users = models.ManyToManyField(
        'User',
        blank=True,
        related_name='assigned_devices',
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['unit_id']

    def __str__(self):
        return f"Unit {self.unit_id}" + (f" – {self.name}" if self.name else "")

    def get_display_name(self):
        return self.name if self.name else f"Unit {self.unit_id}"