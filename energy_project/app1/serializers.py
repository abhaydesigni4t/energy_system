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
    today_kwh      = serializers.FloatField()
    week_kwh       = serializers.FloatField()
    month_kwh      = serializers.FloatField()
    year_kwh       = serializers.FloatField()      # NEW
    lifetime_kwh   = serializers.FloatField()      # NEW
    peak_demand_kw = serializers.FloatField()

class GraphDataSerializer(serializers.Serializer):
    labels = serializers.ListField(child=serializers.CharField())
    values = serializers.ListField(child=serializers.FloatField())
    unit = serializers.CharField()
    

class UtilityMeterReadingSerializer(serializers.ModelSerializer):
    """
    Handles the pre-calculated utility-meter payload. Writes directly
    into SensorReading — no calculation performed here. JSON keys match
    the utility-meter payload exactly; where a key maps onto an existing
    SensorReading field (reused, not duplicated), it's aliased via
    source=.
    """
    voltage_avg_ln = serializers.FloatField(source='voltage')
    current_avg    = serializers.FloatField(source='current')
    kw_total       = serializers.FloatField(source='active_power_kw')
    kvar_total     = serializers.FloatField(source='reactive_power_kvar')
    kva_total      = serializers.FloatField(source='apparent_power_kva')
    kwh_delivered  = serializers.FloatField(source='energy_today_kwh')
 
    _scale_pvs = serializers.IntegerField(source='scale_pvs', default=10)
    _scale_pis = serializers.IntegerField(source='scale_pis', default=10)
    _scale_pns = serializers.IntegerField(source='scale_pns', default=10)
    _scale_pps = serializers.IntegerField(source='scale_pps', default=10)
 
    class Meta:
        model = SensorReading
        fields = [
            'id', 'unit_id', 'timestamp',
            'voltage_avg_ln', 'voltage_a_n', 'voltage_b_n', 'voltage_c_n',
            'voltage_a_b', 'voltage_b_c', 'voltage_c_a', 'voltage_avg_ll',
            'current_avg', 'current_a', 'current_b', 'current_c',
            'current_demand', 'current_peak_demand', 'current_neutral',
            'frequency_hz', 'pf_total', 'pf_a', 'pf_b', 'pf_c',
            'kw_total', 'kvar_total', 'kva_total',
            'kw_a', 'kw_b', 'kw_c', 'kvar_a', 'kvar_b', 'kvar_c',
            'kva_a', 'kva_b', 'kva_c',
            'kw_demand', 'kw_peak_demand', 'kvar_demand', 'kva_demand',
            'kvar_peak_demand', 'kva_peak_demand',
            'kwh_delivered', 'kwh_received', 'kvarh_delivered',
            'kvarh_received', 'kvah_total',
            '_scale_pvs', '_scale_pis', '_scale_pns', '_scale_pps',
            'created_at',
        ]
        read_only_fields = ['id', 'created_at']
 
    def create(self, validated_data):
        # Utility meters have no raw registers -- empty lists so
        # SensorReading.save()'s register-decode block safely no-ops
        # and leaves the directly-provided values untouched.
        validated_data['block1_raw'] = []
        validated_data['block2_raw'] = []
        return super().create(validated_data)
 
    def to_representation(self, instance):
        data = super().to_representation(instance)
        data['_scale_pvs'] = instance.scale_pvs
        data['_scale_pis'] = instance.scale_pis
        data['_scale_pns'] = instance.scale_pns
        data['_scale_pps'] = instance.scale_pps
        return data

    
class SensorReadingFilterSerializer(serializers.ModelSerializer):
    class Meta:
        model = SensorReading
        fields = [
            'id', 'unit_id', 'timestamp', 'created_at',
            'voltage', 'current', 'active_power_kw',
            'reactive_power_kvar', 'apparent_power_kva', 'energy_today_kwh',
        ]