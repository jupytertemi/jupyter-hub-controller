from rest_framework.pagination import CursorPagination, LimitOffsetPagination


class Pagination(LimitOffsetPagination):
    default_limit = 10


class CustomCursorPagination(CursorPagination):
    page_size = 10  # Number of items per page
    ordering = "-id"  # Order results by this field
    cursor_query_param = "cursor"
    page_size_query_param = "page_size"
    max_page_size = 500
