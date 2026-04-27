from rest_framework import serializers
from .models import SensorReading


class SensorReadingSerializer(serializers.ModelSerializer):

    class Meta:
        model  = SensorReading
        # Exactly the 6 fields — nothing more, nothing less
        fields = [
            "unit_id",
            "timestamp",
            "block1_start_reg",
            "block1_raw",
            "block2_start_reg",
            "block2_raw",
        ]

    # Disable auto unique validator so upsert (update) works on POST
    def get_validators(self):
        return []

    # ---------- field-level validation ----------

    def validate_block1_raw(self, value):
        if not isinstance(value, list) or not all(isinstance(v, int) for v in value):
            raise serializers.ValidationError("block1_raw must be a list of integers.")
        return value

    def validate_block2_raw(self, value):
        if not isinstance(value, list) or not all(isinstance(v, int) for v in value):
            raise serializers.ValidationError("block2_raw must be a list of integers.")
        return value

    def validate_timestamp(self, value):
        if value <= 0:
            raise serializers.ValidationError("timestamp must be a positive Unix epoch value.")
        return value