from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)

    if response is not None and "non_field_errors" in response.data:
        if isinstance(response.data["non_field_errors"], list):
            response.data["non_field_errors"] = response.data["non_field_errors"][0]

    return response
