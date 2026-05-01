# serializers.py
from rest_framework import serializers
from .models import SensorReading, HourlyAggregate

class SensorReadingSerializer(serializers.ModelSerializer):
    class Meta:
        model = SensorReading
        fields = [
            'id', 'unit_id', 'timestamp', 'block1_start_reg', 'block1_raw',
            'block2_start_reg', 'block2_raw', 'voltage', 'current', 
            'active_power_kw', 'reactive_power_kvar', 'apparent_power_kva', 
            'energy_today_kwh', 'created_at'
        ]
        read_only_fields = ['id', 'created_at', 'voltage', 'current', 
                           'active_power_kw', 'reactive_power_kvar', 
                           'apparent_power_kva', 'energy_today_kwh']

class LatestReadingSerializer(serializers.Serializer):
    voltage = serializers.FloatField()
    current = serializers.FloatField()
    active_power_kw = serializers.FloatField()
    reactive_power_kvar = serializers.FloatField()
    apparent_power_kva = serializers.FloatField()
    energy_today_kwh = serializers.FloatField()
    timestamp = serializers.CharField()
    status = serializers.CharField()

class SummaryStatsSerializer(serializers.Serializer):
    today_kwh = serializers.FloatField()
    week_kwh = serializers.FloatField()
    month_kwh = serializers.FloatField()
    peak_demand_kw = serializers.FloatField()

class GraphDataSerializer(serializers.Serializer):
    labels = serializers.ListField(child=serializers.CharField())
    values = serializers.ListField(child=serializers.FloatField())
    unit = serializers.CharField()