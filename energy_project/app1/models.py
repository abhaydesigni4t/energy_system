from django.db import models


class SensorReading(models.Model):
    """
    One record per unit_id (unique).
    POSTing an existing unit_id performs an in-place update (upsert).
    """
    unit_id          = models.PositiveIntegerField(unique=True, db_index=True)  # ← UNIQUE
    timestamp        = models.FloatField()
    block1_start_reg = models.PositiveIntegerField()
    block1_raw       = models.JSONField()                          # list[int]
    block2_start_reg = models.PositiveIntegerField()
    block2_raw       = models.JSONField()                          # list[int]
    created_at       = models.DateTimeField(auto_now_add=True)
    updated_at       = models.DateTimeField(auto_now=True)         # tracks last upsert

    class Meta:
        ordering = ["-updated_at"]

    def __str__(self):
        return f"Unit {self.unit_id} @ {self.timestamp}"