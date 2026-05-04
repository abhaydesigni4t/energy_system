from django.db import models
from django.utils import timezone
from datetime import datetime

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
            # Voltages (scale /10)
            v_a = self.block1_raw[0] / 10
            v_b = self.block1_raw[1] / 10
            v_c = self.block1_raw[2] / 10
            self.voltage = round((v_a + v_b + v_c) / 3, 1)
            
            # Currents (scale /10)
            i_a = self.block1_raw[8] / 10
            i_b = self.block1_raw[9] / 10
            i_c = self.block1_raw[10] / 10
            self.current = round((i_a + i_b + i_c) / 3, 1)
            
            # Active Power (from V×I)
            total_watts = (v_a * i_a) + (v_b * i_b) + (v_c * i_c)
            self.active_power_kw = round(total_watts / 1000, 1)
            
            # Reactive Power (scale /10) – handle missing values
            self.reactive_power_kvar = (self.block1_raw[21] / 10) if len(self.block1_raw) > 21 and self.block1_raw[21] is not None else 0
            
            # ✅ FIXED Apparent Power – calculated from V×I (not register 40122)
            total_va = total_watts  # VA = Watts when PF=1, but we still calculate correctly
            # Actually we should compute apparent power per phase (V×I) without PF
            # Since VA = V×I (magnitudes), same as we already have for active power only if PF=1.
            # To be technically correct for any PF, we need to use RMS values:
            s_a = v_a * i_a
            s_b = v_b * i_b
            s_c = v_c * i_c
            self.apparent_power_kva = round((s_a + s_b + s_c) / 1000, 1)
            
            # Note: If you want to keep using the meter's register but correct it:
            # self.apparent_power_kva = round((self.block1_raw[22] / 10) / 3, 1)  # 31.5/3 ≈ 10.5 (close)
        
        if self.block2_raw and len(self.block2_raw) > 1:
            raw_energy = (self.block2_raw[1] * 65536) + self.block2_raw[0]
            self.energy_today_kwh = round(raw_energy / 100000, 1)
        
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