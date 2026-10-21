from django.db import models
from django.utils import timezone
from datetime import datetime
from django.contrib.auth.models import AbstractUser
from django.db import models


from django.contrib.auth.models import UserManager as BaseUserManager

from django.core.validators import RegexValidator

UNIT_ID_VALIDATOR = RegexValidator(
    r'^[A-Za-z0-9_-]+$',
    'Unit ID may contain only letters, numbers, hyphens, and underscores (no spaces).'
)


from django.core.validators import RegexValidator

BILLING_PREFIX_VALIDATOR = RegexValidator(
    regex=r'^[A-Za-z]{5,6}$',
    message='Billing prefix must be 5–6 letters (letters only, no numbers or symbols).'
)

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
        ('operator', 'Operator'),
        ('user', 'User'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='user')
    unit_ids = models.JSONField(
        default=list,
        blank=True,
        help_text="List of unit IDs this user has access to"
    )
    company = models.ForeignKey(
        'Company',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='members',
        help_text="Company this user belongs to. Required for Admin/User roles; ignored for Super Admin.",
    )
    
    companies = models.ManyToManyField(
        'Company',
        blank=True,
        related_name='team_members',
        help_text=(
            "Microgrids this user can access. For Admin: the microgrids they manage. "
            "For User/Operator: the specific microgrids they've been granted view access to."
        ),
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
    
    phone = models.CharField(
        max_length=20, blank=True,
    )
    contact_preference = models.CharField(
        max_length=10,
        choices=[('email', 'Email'), ('phone', 'Phone')],
        default='email',
        help_text="How this Person prefers to receive verification codes.",
    )
    
    SIGNUP_INTENT_CHOICES = [
        ('design', 'Design a Microgrid'),
        ('operate', 'Operate Microgrid (VPP)'),
        ('commission', 'Set Up, Configure & Commission a System'),
    ]
    signup_intent = models.CharField(
        max_length=20, choices=SIGNUP_INTENT_CHOICES, blank=True,
        help_text="Which of the 3 landing tiles this Person originally clicked."
    )
    

    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def is_superadmin(self):
        return self.role == 'superadmin'

    def is_admin_or_superadmin(self):
        return self.role in ('admin', 'superadmin')

    def is_operator(self):
        return self.role == 'operator'

    def is_view_only(self):
        """User or Operator — never has write access."""
        return self.role in ('user', 'operator')

    def get_unit_ids(self):
        return self.unit_ids if self.unit_ids else []
    
    def get_accessible_customer_ids(self):
        """Customer IDs reachable through any of this user's assigned microgrids."""
        if self.role == 'superadmin':
            return list(Customer.objects.values_list('id', flat=True))
        company_ids = self.get_accessible_company_ids()
        return list(
            Customer.objects.filter(microgrids__id__in=company_ids)
            .distinct().values_list('id', flat=True)
        )

    def can_access_customer(self, customer_id):
        if self.role == 'superadmin':
            return True
        if customer_id is None:
            return False
        return Customer.objects.filter(
            pk=customer_id, microgrids__in=self.companies.all()
        ).exists()

    def get_accessible_company_ids(self):
        """List of Company IDs this user can see, regardless of role."""
        if self.role == 'superadmin':
            return list(Company.objects.values_list('id', flat=True))
        return list(self.companies.values_list('id', flat=True))

    def get_accessible_unit_ids(self):
        """
        Superadmin -> every active unit_id in the system.
        Admin/User/Operator -> every active unit_id belonging to any of
        their assigned companies (microgrids). Falls back to legacy
        unit_ids if they have zero companies assigned (not yet migrated).
        """
        if self.role == 'superadmin':
            return list(Device.objects.filter(is_active=True).values_list('unit_id', flat=True))
        company_ids = self.get_accessible_company_ids()
        if company_ids:
            return list(
                Device.objects.filter(company_id__in=company_ids, is_active=True)
                .values_list('unit_id', flat=True)
            )
        return self.get_unit_ids()

    def can_access_unit(self, unit_id):
        if self.role == 'superadmin':
            return True
        return str(unit_id) in [str(u) for u in self.get_accessible_unit_ids()]

    def can_access_company(self, company_id):
        if self.role == 'superadmin':
            return True
        if company_id is None:
            return False
        return self.companies.filter(pk=company_id).exists()

    def can_write(self):
        """True for Admin/Super Admin only. User/Operator are always view-only."""
        return self.role in ('admin', 'superadmin')
    
    def save(self, *args, **kwargs):
        # Auto-set role for superusers
        if self.is_superuser and self.role == 'user':
            self.role = 'superadmin'
        super().save(*args, **kwargs)

    def get_display_name(self):
        """Clean name for screens: full name, else email. Never the internal username."""
        return self.get_full_name().strip() or self.email or self.username

    def __str__(self):
        return f"{self.get_display_name()} ({self.role})"
    
import random

class OTPCode(models.Model):
    """
    One-time verification code for the login flow. Not tied to sessions —
    looked up by email so it survives cookie/session edge cases.
    """
    PURPOSE_CHOICES = [('login', 'Login Verification')]

    email = models.EmailField(db_index=True)
    code = models.CharField(max_length=6)
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default='login')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)
    attempts = models.PositiveIntegerField(default=0)

    class Meta:
        indexes = [models.Index(fields=['email', 'is_used'])]
        ordering = ['-created_at']

    @classmethod
    def generate_for(cls, email, purpose='login', ttl_minutes=10):
        code = f"{random.randint(0, 999999):06d}"
        return cls.objects.create(
            email=email.lower(),
            code=code,
            purpose=purpose,
            expires_at=timezone.now() + timezone.timedelta(minutes=ttl_minutes),
        )

    def is_valid(self):
        """Not used, not expired, and hasn't been guessed at too many times."""
        return (not self.is_used) and timezone.now() < self.expires_at and self.attempts < 5

    def __str__(self):
        return f"OTP({self.email}, used={self.is_used})"
  
    
class SensorReading(models.Model):
    """
    Stores each Modbus reading as a separate record for historical data.
    Also used for utility meters (pre-calculated values) — those rows
    simply leave block1_raw/block2_raw empty and the new per-phase/
    demand/extra-energy fields populated directly.
    """
    unit_id = models.CharField(max_length=50, db_index=True)
    timestamp = models.FloatField(help_text="Unix timestamp")
    block1_start_reg = models.PositiveIntegerField(default=40100)
    block1_raw = models.JSONField(help_text="Raw registers from block 1", default=list, blank=True)
    block2_start_reg = models.PositiveIntegerField(default=40138)
    block2_raw = models.JSONField(help_text="Raw registers from block 2", default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
 
    # ── Existing cached/decoded values — UNCHANGED, reused for utility meters too ──
    voltage = models.FloatField(null=True, blank=True)
    current = models.FloatField(null=True, blank=True)
    active_power_kw = models.FloatField(null=True, blank=True)
    reactive_power_kvar = models.FloatField(null=True, blank=True)
    apparent_power_kva = models.FloatField(null=True, blank=True)
    energy_today_kwh = models.FloatField(null=True, blank=True)
 
    # ── NEW: per-phase voltage (nullable — only utility meters populate these) ──
    voltage_a_n = models.FloatField(null=True, blank=True)
    voltage_b_n = models.FloatField(null=True, blank=True)
    voltage_c_n = models.FloatField(null=True, blank=True)
    voltage_a_b = models.FloatField(null=True, blank=True)
    voltage_b_c = models.FloatField(null=True, blank=True)
    voltage_c_a = models.FloatField(null=True, blank=True)
    voltage_avg_ll = models.FloatField(null=True, blank=True)
 
    # ── NEW: per-phase current ──
    current_a = models.FloatField(null=True, blank=True)
    current_b = models.FloatField(null=True, blank=True)
    current_c = models.FloatField(null=True, blank=True)
    current_demand = models.FloatField(null=True, blank=True)
    current_peak_demand = models.FloatField(null=True, blank=True)
    current_neutral = models.FloatField(null=True, blank=True)
 
    # ── NEW: frequency / power factor ──
    frequency_hz = models.FloatField(null=True, blank=True)
    pf_total = models.FloatField(null=True, blank=True)
    pf_a = models.FloatField(null=True, blank=True)
    pf_b = models.FloatField(null=True, blank=True)
    pf_c = models.FloatField(null=True, blank=True)
 
    # ── NEW: per-phase power ──
    kw_a = models.FloatField(null=True, blank=True, default=0.0)
    kw_b = models.FloatField(null=True, blank=True, default=0.0)
    kw_c = models.FloatField(null=True, blank=True, default=0.0)
    kvar_a = models.FloatField(null=True, blank=True, default=0.0)
    kvar_b = models.FloatField(null=True, blank=True, default=0.0)
    kvar_c = models.FloatField(null=True, blank=True, default=0.0)
    kva_a = models.FloatField(null=True, blank=True, default=0.0)
    kva_b = models.FloatField(null=True, blank=True, default=0.0)
    kva_c = models.FloatField(null=True, blank=True, default=0.0)
 
    # ── NEW: power demand ──
    kw_demand = models.FloatField(null=True, blank=True, default=0.0)
    kw_peak_demand = models.FloatField(null=True, blank=True, default=0.0)
    kvar_demand = models.FloatField(null=True, blank=True, default=0.0)
    kva_demand = models.FloatField(null=True, blank=True, default=0.0)
    kvar_peak_demand = models.FloatField(null=True, blank=True, default=0.0)
    kva_peak_demand = models.FloatField(null=True, blank=True, default=0.0)
 
    # ── NEW: additional energy counters (energy_today_kwh already covers "delivered") ──
    kwh_received = models.BigIntegerField(null=True, blank=True, default=0)
    kvarh_delivered = models.BigIntegerField(null=True, blank=True, default=0)
    kvarh_received = models.BigIntegerField(null=True, blank=True, default=0)
    kvah_total = models.BigIntegerField(null=True, blank=True, default=0)
 
    # ── NEW: scale-factor metadata ──
    scale_pvs = models.IntegerField(null=True, blank=True, default=10)
    scale_pis = models.IntegerField(null=True, blank=True, default=10)
    scale_pns = models.IntegerField(null=True, blank=True, default=10)
    scale_pps = models.IntegerField(null=True, blank=True, default=10)
 
    class Meta:
        ordering = ['-timestamp']
        indexes = [
            models.Index(fields=['unit_id', 'timestamp']),
            models.Index(fields=['-timestamp']),
        ]
 
    def save(self, *args, **kwargs):
        """
        Decode all block1_raw registers and cache key values, then bake
        this DER's scaling factor (whatever was effective at THIS
        reading's own timestamp) directly into active_power_kw /
        energy_today_kwh.

        block1_raw / block2_raw are NEVER modified — they remain the
        permanent original meter/register values, decodable at any time.

        kW is scaled independently per row (each row stands alone, no
        dependency on other rows).

        kWh is a running cumulative counter, so it can't just be scaled
        independently per row — that would make the counter JUMP the
        instant a factor changes. Instead we scale the RAW INCREMENT
        since the previous reading, and add that scaled increment to the
        previous reading's already-STORED (already-scaled) value. This
        keeps the counter perfectly continuous across a factor change —
        only the increment recorded after a change uses the new factor,
        and everything before it is untouched.

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
        [20]    Total Active Power   (signed 16-bit ÷ 100 → kW)
        [21]    Total Reactive Power (signed 16-bit ÷ 10 → kVAR)
        [22]    Total Apparent Power (÷100 → kVA, always positive)

        block2_raw:
        [0]     Energy low word
        [1]     Energy high word
        (32-bit combined, SCALE = 1 — matches existing behavior, unchanged)

        For utility-meter rows, block1_raw/block2_raw are empty lists,
        so this whole method safely no-ops and leaves whatever values
        were already set on the instance untouched.
        """

        def to_signed_16(raw):
            if raw > 32767:
                return raw - 65536
            return raw

        device = Device.objects.filter(unit_id=self.unit_id).only('id', 'scaling_factor').first()

        if self.block1_raw and len(self.block1_raw) >= 23:
            b = self.block1_raw

            self.voltage = round(b[3] / 10, 1)
            self.current = round(b[11] / 10, 1)

            raw_kw = to_signed_16(b[20]) / 100
            scale = device.get_scale_factor_at(self.timestamp) if device else 1.0
            self.active_power_kw = round(raw_kw * scale, 2)

            self.reactive_power_kvar = round(to_signed_16(b[21]) / 10, 2)
            self.apparent_power_kva = round(b[22] / 100, 2)

        if self.block2_raw and len(self.block2_raw) >= 2:
            low = self.block2_raw[0]
            high = self.block2_raw[1]
            raw_cumulative = ((high << 16) | low) / 1   # SCALE = 1, unchanged from original

            scale = device.get_scale_factor_at(self.timestamp) if device else 1.0

            prev = (
                SensorReading.objects
                .filter(unit_id=self.unit_id, timestamp__lt=self.timestamp)
                .exclude(pk=self.pk)
                .order_by('-timestamp')
                .first()
            )

            if prev is not None and prev.block2_raw and len(prev.block2_raw) >= 2:
                prev_low = prev.block2_raw[0]
                prev_high = prev.block2_raw[1]
                prev_raw_cumulative = ((prev_high << 16) | prev_low) / 1
                prev_stored_kwh = prev.energy_today_kwh if prev.energy_today_kwh is not None else prev_raw_cumulative

                raw_increment = max(0.0, raw_cumulative - prev_raw_cumulative)
                self.energy_today_kwh = round(prev_stored_kwh + raw_increment * scale, 3)
            else:
                # First-ever reading for this unit — nothing to carry forward from.
                self.energy_today_kwh = round(raw_cumulative * scale, 3)

        super().save(*args, **kwargs)

    def __str__(self):
        return f"Unit {self.unit_id} @ {datetime.fromtimestamp(self.timestamp)}"

class HourlyAggregate(models.Model):
    """
    Pre-calculated hourly averages for graph performance
    """
    unit_id = models.CharField(max_length=50, db_index=True)
    hour = models.DateTimeField(db_index=True)
    avg_active_power_kw = models.FloatField(default=0)
    min_active_power_kw = models.FloatField(default=0)
    max_active_power_kw = models.FloatField(default=0)
    total_energy_kwh = models.FloatField(default=0)
    reading_count = models.PositiveIntegerField(default=0)
    
    class Meta:
        unique_together = [['unit_id', 'hour']]
        ordering = ['-hour']
        
        
class InverterReading(models.Model):
    """
    Full telemetry snapshot from a string inverter (inverter, etc.).
    Kept separate from SensorReading because this payload is much richer —
    three-phase AC values, DC input, cabinet temperature, and a variable
    number of MPPT strings — not something SensorReading's simple
    block1_raw/block2_raw register shape was built for.

    This is the detailed record for diagnostics. A simplified copy also
    gets written to SensorReading (see post_inverter_reading view) so the
    existing dashboard/billing/revenue code works for this device with no
    changes needed.
    """
    unit_id = models.CharField(max_length=50, db_index=True)
    device_type = models.CharField(max_length=30, default='inverter')
    timestamp = models.FloatField(db_index=True)  # epoch seconds, same convention as SensorReading
    modbus_address = models.IntegerField(null=True, blank=True)
    operating_state = models.CharField(max_length=50, blank=True, null=True)

    # AC side — three-phase, flattened into fixed columns (always exactly
    # 3 phases, so this is easier to query/chart than a nested JSON blob)
    ac_total_current_a = models.FloatField(null=True, blank=True)
    ac_phase_a_current_a = models.FloatField(null=True, blank=True)
    ac_phase_b_current_a = models.FloatField(null=True, blank=True)
    ac_phase_c_current_a = models.FloatField(null=True, blank=True)

    ac_phase_a_voltage_v = models.FloatField(null=True, blank=True)  # phase-to-neutral
    ac_phase_b_voltage_v = models.FloatField(null=True, blank=True)
    ac_phase_c_voltage_v = models.FloatField(null=True, blank=True)

    ac_phase_ab_voltage_v = models.FloatField(null=True, blank=True)  # phase-to-phase
    ac_phase_bc_voltage_v = models.FloatField(null=True, blank=True)
    ac_phase_ca_voltage_v = models.FloatField(null=True, blank=True)

    ac_active_power_w = models.FloatField(null=True, blank=True)
    ac_apparent_power_va = models.FloatField(null=True, blank=True)
    ac_reactive_power_var = models.FloatField(null=True, blank=True)
    power_factor = models.FloatField(null=True, blank=True)
    grid_frequency_hz = models.FloatField(null=True, blank=True)

    lifetime_energy_kwh = models.FloatField(null=True, blank=True)

    # DC side (combined/total across all strings)
    dc_total_current_a = models.FloatField(null=True, blank=True)
    dc_voltage_v = models.FloatField(null=True, blank=True)
    dc_power_w = models.FloatField(null=True, blank=True)

    cabinet_temperature_c = models.FloatField(null=True, blank=True)

    mppt_total_power_w = models.FloatField(null=True, blank=True)
    # Variable number of MPPT strings depending on inverter model — stored
    # as JSON exactly as received: [{input_id, current_a, voltage_v, power_w, state, raw_events}, ...]
    mppt_inputs = models.JSONField(default=list, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [models.Index(fields=['unit_id', '-timestamp'])]
        ordering = ['-timestamp']

    def __str__(self):
        return f"{self.unit_id} @ {self.timestamp}"
        
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
        
class Customer(models.Model):
    """
    The real client organization (e.g. Atlantic County, MPWC).
    Owns one or more Microgrids (Company rows).
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
        null=True, blank=True,
        related_name='created_customers',
    )

    class Meta:
        verbose_name = 'Customer'
        verbose_name_plural = 'Customers'
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_microgrid_count(self):
        return self.microgrids.count()

    def get_device_count(self):
        return Device.objects.filter(company__customer=self).count()   

class Company(models.Model):
    name = models.CharField(max_length=200, unique=True)   # was: max_length=200 (no unique)
    location = models.CharField(max_length=300, blank=True)
    description = models.TextField(blank=True)
    contact_email = models.EmailField(blank=True)
    contact_phone = models.CharField(max_length=50, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        'User', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='created_companies',
    )
    customer = models.ForeignKey(
        'Customer', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='microgrids',
        help_text='The customer this microgrid site belongs to (e.g. Atlantic County).',
    )

    class Meta:
        verbose_name = 'Company'
        verbose_name_plural = 'Companies'
        ordering = ['name']

    def __str__(self):
        return self.name

    def get_device_count(self):
        return self.devices.count()


"""
In models.py, add DEVICE_CATEGORY_CHOICES before the Device class,
then replace the Device class entirely with this version.
"""

DEVICE_CATEGORY_CHOICES = [
    ('Meters', 'Meters'),
    ('Solar Generation', 'Solar Generation'),
    ('Battery Power', 'Battery Power'),
    ('EV Charging', 'EV Charging'),
    ('Building Load', 'Building Load'),
    ('HVAC', 'HVAC'),
    ('Genset', 'Genset'),
]


class Device(models.Model):
    """
    Represents a physical Modbus unit (energy meter / sensor).
    unit_id must match what the SBC sends in readings.
    """
    unit_id = models.CharField(
        max_length=50, unique=True, db_index=True,
        validators=[UNIT_ID_VALIDATOR],
        help_text='Alphanumeric ID that must match what the SBC sends (e.g. 100, ABC101, Unit100).',
    )
    device_name = models.CharField(
        max_length=200,
        blank=True,
        default='',
        help_text='Human-readable display name shown in the UI (e.g. "Main Panel"). Falls back to unit_id if blank.',
    )
    device_category = models.CharField(
        max_length=50,
        choices=DEVICE_CATEGORY_CHOICES,
        default='Meters',
        help_text='Category determines which navbar dropdown this device appears under.',
    )
    per_unit_cost = models.DecimalField(
        max_digits=10, decimal_places=4,
        null=True, blank=True,
        help_text='Cost per kWh for financial calculations (optional).',
    )
    
    escalator = models.DecimalField(
        max_digits=6, decimal_places=4, null=True, blank=True,
        help_text='Annual price escalator as a percentage (e.g. 2.5 = 2.5%/yr). Metadata only.',
    )
    effective_date = models.DateField(
        null=True, blank=True,
        help_text='Date from which the cost per kWh / escalator becomes effective.',
    )
    odometer_offset = models.DecimalField(
        max_digits=14, decimal_places=3,
        default=0,
        help_text='kWh offset added to raw meter readings to align the cumulative odometer '
                  '(e.g. when replacing a meter that already had usage).',
    )
    
    odometer_offset_datetime = models.DateTimeField(
        null=True, blank=True,
        help_text='Date & time the odometer offset was captured / applied.',
    )
    
    gats_offset = models.DecimalField(
        max_digits=14, decimal_places=3,
        default=0,
        help_text='kWh offset used ONLY to align this device\'s cumulative reading with '
                  'the GATS server\'s own reported meter total. Independent of '
                  'odometer_offset — does not affect billing, dashboard, or any other calculation.',
    )

    gats_offset_datetime = models.DateTimeField(
        null=True, blank=True,
        help_text='Date & time the GATS offset was captured / calculated.',
    )
    
    description = models.TextField(blank=True)
    location = models.CharField(max_length=300, blank=True)
    scaling_factor = models.DecimalField(
        max_digits=8, decimal_places=4,
        default=1.0,
        help_text='Multiplier applied to this device\'s kW/kWh values ONLY on '
                'display (graphs, tiles, CSV). Raw DB values are never modified. '
                'e.g. 0.5 halves everything shown for this device.'
    )
    billing_account_number = models.CharField(
        max_length=30, blank=True,
        help_text='Utility billing account # shown on generated bills.',
    )
    billing_routing_number = models.CharField(
        max_length=20, blank=True, default='121000358',
    )
    
    billing_prefix = models.CharField(
        max_length=6,
        blank=True,
        null=True,
        validators=[BILLING_PREFIX_VALIDATOR],
        help_text='Billing reference code.',
    )
    company = models.ForeignKey(
        Company,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='devices',
    )
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
        label = getattr(self, 'device_name', None) or getattr(self, 'name', None) or str(self.unit_id)
        return f"{label} [{self.device_category}]"

    def get_display_name(self):
        # device_name (new) → name (legacy, may not exist) → unit_id
        if getattr(self, 'device_name', None):
            return self.device_name
        if getattr(self, 'name', None):
            return self.name
        return str(self.unit_id)
    
    def get_scale_factor(self):
        try:
            return float(self.scaling_factor) if self.scaling_factor is not None else 1.0
        except (TypeError, ValueError):
            return 1.0

    def scale_value(self, value):
        """Apply this device's display scale factor. None passes through untouched."""
        if value is None:
            return None
        return round(value * self.get_scale_factor(), 3)
    
    def get_scale_factor_at(self, ts):
        """
        Scale factor in effect at unix timestamp `ts`. Used ONLY inside
        SensorReading.save() at write time. Falls back to 1.0 (no
        correction) if this device has no history entries yet — a brand
        new device is never scaled until someone explicitly sets a factor.
        """
        entry = self.scaling_history.filter(effective_from_ts__lte=ts).order_by('-effective_from_ts').first()
        return float(entry.scale_factor) if entry else 1.0

class DeviceScalingFactorHistory(models.Model):
    """
    Audit trail of scaling-factor changes for a DER. Every entry records
    a scale and the exact moment it started applying. SensorReading.save()
    looks up the factor effective at a reading's OWN timestamp and bakes
    the corrected value directly into active_power_kw / energy_today_kwh.
    block1_raw / block2_raw are never touched, so the original meter
    values are always recoverable from raw registers.
    """
    device = models.ForeignKey(Device, on_delete=models.CASCADE, related_name='scaling_history')
    scale_factor = models.DecimalField(max_digits=8, decimal_places=4)
    effective_from_ts = models.FloatField(
        help_text="Unix timestamp. Readings at/after this moment use this "
                  "factor, until a newer entry supersedes it."
    )
    note = models.CharField(max_length=255, blank=True)
    changed_by = models.ForeignKey(
        'User', null=True, blank=True, on_delete=models.SET_NULL,
        related_name='scaling_factor_changes',
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-effective_from_ts']
        indexes = [models.Index(fields=['device', '-effective_from_ts'])]

    def __str__(self):
        return f"{self.device.unit_id}: ×{self.scale_factor} from {datetime.fromtimestamp(self.effective_from_ts)}"
    
    
class InvoiceSequence(models.Model):
    """
    Tracks the next serial number for invoices generated on a given date.
    date_key format: MMDDYYYY (matches the invoice reference number prefix).
    """
    date_key = models.CharField(max_length=8, unique=True, db_index=True)
    last_serial = models.PositiveIntegerField(default=0)

    def __str__(self):
        return f"{self.date_key} → last used #{self.last_serial:02d}"
    
    
class BillLogo(models.Model):
    """
    Singleton-style model holding the current invoice logo.
    Only one active row is expected; admin uploads replace it.
    """
    logo = models.ImageField(upload_to='bill_logo/')
    uploaded_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Bill Logo"
        verbose_name_plural = "Bill Logo"

    def __str__(self):
        return f"Bill logo (updated {self.uploaded_at.strftime('%Y-%m-%d %H:%M')})"

    @classmethod
    def get_current(cls):
        """Returns the most recently uploaded logo, or None if none exists."""
        return cls.objects.order_by('-uploaded_at').first()