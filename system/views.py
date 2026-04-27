from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import AllowAny

class SystemTemperatureView(APIView):
    permission_classes = [AllowAny]
    
    def get(self, request):
        try:
            # Read CPU temperature (in millidegrees)
            with open('/sys/class/thermal/thermal_zone1/temp', 'r') as f:
                temp_milli = int(f.read().strip())
            
            # Convert to Celsius
            temp_celsius = temp_milli / 1000.0
            
            return Response({
                'success': True,
                'temperature': round(temp_celsius, 1),
                'unit': 'celsius'
            })
        except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=500)


class SystemUptimeView(APIView):
    permission_classes = [AllowAny]
    
    def get(self, request):
        try:
            # Read system uptime from /proc/uptime
            with open('/proc/uptime', 'r') as f:
                uptime_seconds = float(f.read().split()[0])
            
            return Response({
                'success': True,
                'uptime': int(uptime_seconds),
                'unit': 'seconds'
            })
        except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=500)
