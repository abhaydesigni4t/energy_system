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
        """
        Decode all block1_raw registers and cache key values.

        Register map (block1_raw indices):
        [0]     Phase A to Neutral voltage  (÷10 → Volts)
        [1]     Phase B to Neutral voltage  (÷10 → Volts)
        [2]     Phase C to Neutral voltage  (÷10 → Volts)
        [3]     Average Line-Neutral voltage (÷10 → Volts)  ← meter's own average
        [4]     Voltage A-B  (÷10 → Volts)
        [5]     Voltage B-C  (÷10 → Volts)
        [6]     Voltage C-A  (÷10 → Volts)
        [7]     Average Line-Line voltage  (÷10 → Volts)
        [8]     Current Phase A  (÷10 → Amps)
        [9]     Current Phase B  (÷10 → Amps)
        [10]    Current Phase C  (÷10 → Amps)
        [11]    Average Current  (÷10 → Amps)  ← meter's own average
        [12]    Demand Current   (÷10 → Amps)
        [13]    Peak Demand Current (÷10 → Amps)
        [14]    Neutral Current  (÷10 → Amps)
        [15]    Frequency        (÷100 → Hz)
        [16]    Total Power Factor  (signed 16-bit ÷ 10000 → -1.0 to +1.0)
        [17]    Power Factor Phase A (signed 16-bit ÷ 10000)
        [18]    Power Factor Phase B (signed 16-bit ÷ 10000)
        [19]    Power Factor Phase C (signed 16-bit ÷ 10000)
        [20]    Total Active Power   (signed 16-bit ÷ 10 → kW)
        [21]    Total Reactive Power (signed 16-bit ÷ 10 → kVAR)
        [22]    Total Apparent Power (÷10 → kVA, always positive)

        block2_raw:
        [0]     Energy low word  (32-bit combined ÷ 100 → kWh)
        [1]     Energy high word
        """

        def to_signed_16(raw):
            """
            Convert unsigned 16-bit Modbus register to signed integer.
            Modbus sends all registers as unsigned (0–65535).
            Values above 32767 represent negative numbers in two's complement:
                0–32767   → positive as-is
                32768–65535 → subtract 65536 to get negative value
            Examples:
                raw=13    → 13       (positive, no conversion needed)
                raw=64536 → -1000    (64536 - 65536 = -1000)
                raw=64391 → -1145    (64391 - 65536 = -1145)
            """
            if raw > 32767:
                return raw - 65536
            return raw

        if self.block1_raw and len(self.block1_raw) >= 23:
            b = self.block1_raw

            # ── Voltage Phase-to-Neutral (÷10 → Volts) ───────────────────
            # These measure voltage from each phase wire to the neutral wire.
            # A 480V 3-phase system has ~277V phase-to-neutral (277 × √3 = 480).
            # Your readings show ~280V L-N and ~485V L-L which is correct.
            v_a = b[0] / 10   # Phase A to Neutral
            v_b = b[1] / 10   # Phase B to Neutral
            v_c = b[2] / 10   # Phase C to Neutral

            # Use meter's own average (reg[3]) — more accurate than manual average
            # because the meter may apply its own filtering/rounding internally.
            self.voltage = round(b[3] / 10, 1)

            # ── Current (÷10 → Amps) ──────────────────────────────────────
            # Current flowing through each phase conductor.
            # Use meter's own average current (reg[11]) for consistency.
            self.current = round(b[11] / 10, 1)

            # ── Active Power kW (signed, ÷10) ─────────────────────────────
            # Real/active power = actual energy consumed per second (watts).
            # Positive = consuming power (load), Negative = generating (solar/genset).
            # MUST use meter's register [20] directly — NOT calculated from V×I,
            # because V×I = apparent power (kVA), not active power (kW).
            # Verified: raw=13 → signed=13 → 13÷10 = 1.3 kW ✅
            self.active_power_kw = round(to_signed_16(b[20]) / 10, 2)

            # ── Reactive Power kVAR (signed, ÷10) ────────────────────────
            # Power stored/released by inductors and capacitors.
            # Positive = inductive (motors, transformers — most common loads).
            # Negative = capacitive (power factor correction capacitors).
            # Verified: raw=99 → signed=99 → 9.9 kVAR ✅
            self.reactive_power_kvar = round(to_signed_16(b[21]) / 10, 2)

            # ── Apparent Power kVA (unsigned, ÷10) ────────────────────────
            # Vector sum of kW and kVAR. Always positive.
            # kVA = √(kW² + kVAR²)
            # Verified: √(1.3² + 9.9²) = √(1.69 + 98.01) = √99.7 = 9.985 ≈ 9.9 kVA ✅
            self.apparent_power_kva = round(b[22] / 10, 2)

        if self.block2_raw and len(self.block2_raw) >= 2:
            # ── Energy kWh (32-bit combined, ÷100) ────────────────────────
            # Two 16-bit Modbus registers form one 32-bit cumulative counter.
            # Assembly: high_word shifted left 16 bits OR'd with low_word.
            # Example: low=6063, high=0 → (0 << 16) | 6063 = 6063
            #          6063 ÷ 100 = 60.63 kWh ✅
            # SCALE=100 means each count = 0.01 kWh.
            low  = self.block2_raw[0]
            high = self.block2_raw[1]
            raw_32 = (high << 16) | low
            SCALE = 100
            self.energy_today_kwh = raw_32 / SCALE

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