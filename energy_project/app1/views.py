from django.shortcuts import render

# Create your views here.

def energy_dashboard(request):
    return render(request, 'app1/energy_dashboard.html')



from rest_framework.views     import APIView
from rest_framework.response  import Response
from rest_framework           import status

from .models      import SensorReading
from .serializers import SensorReadingSerializer


class SensorReadingAPIView(APIView):
    """
    GET  /api/sensor/readings/            → list all readings
    GET  /api/sensor/readings/?unit_id=X  → get single unit
    POST /api/sensor/readings/            → create OR update (upsert) by unit_id
    """

    def get(self, request):
        unit_id = request.query_params.get("unit_id")

        if unit_id is not None:
            if not unit_id.isdigit():
                return Response(
                    {"error": "unit_id must be a positive integer."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            try:
                instance = SensorReading.objects.get(unit_id=unit_id)
            except SensorReading.DoesNotExist:
                return Response(
                    {"error": f"No record found for unit_id {unit_id}."},
                    status=status.HTTP_404_NOT_FOUND,
                )
            serializer = SensorReadingSerializer(instance)
            return Response(serializer.data, status=status.HTTP_200_OK)

        queryset   = SensorReading.objects.all()
        serializer = SensorReadingSerializer(queryset, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        unit_id = request.data.get("unit_id")

        if unit_id is None:
            return Response(
                {"error": "unit_id is required."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            # unit exists → UPDATE
            instance    = SensorReading.objects.get(unit_id=unit_id)
            serializer  = SensorReadingSerializer(instance, data=request.data)
            http_status = status.HTTP_200_OK
        except SensorReading.DoesNotExist:
            # new unit → CREATE
            serializer  = SensorReadingSerializer(data=request.data)
            http_status = status.HTTP_201_CREATED

        if serializer.is_valid():
            serializer.save()
            # Return exactly the same format that was posted
            return Response(serializer.data, status=http_status)

        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)