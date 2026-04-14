from rest_framework import status
from rest_framework.exceptions import APIException


class CustomException(APIException):
    status_code = status.HTTP_400_BAD_REQUEST
    default_detail = {"error": "Malformed request", "code": status_code}

    def __init__(self, detail=None, code=None):
        if detail is not None:
            self.detail = {"error": str(detail), "code": self.status_code}
        else:
            self.detail = self.default_detail


class StructuredException(CustomException):
    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_detail = {"error": "Invalid request", "code": status_code}


class GoneException(CustomException):
    status_code = status.HTTP_410_GONE
