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
        # Decode registers before saving
        if self.block1_raw and len(self.block1_raw) > 0:
            self.voltage = self.block1_raw[0] / 10 if len(self.block1_raw) > 0 else 0
            self.current = self.block1_raw[8] / 10 if len(self.block1_raw) > 8 else 0
            self.active_power_kw = self.block1_raw[20] if len(self.block1_raw) > 20 else 0
            self.reactive_power_kvar = self.block1_raw[21] if len(self.block1_raw) > 21 else 0
            self.apparent_power_kva = self.block1_raw[22] if len(self.block1_raw) > 22 else 0
        
        if self.block2_raw and len(self.block2_raw) > 1:
            self.energy_today_kwh = (self.block2_raw[1] * 65536) + self.block2_raw[0]
        
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