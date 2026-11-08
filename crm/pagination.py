"""Stronicowanie list API: domyślnie 20 na stronę, a ekran może poprosić o więcej (?page_size=, max 500).

Ekran „Dzwonienie” pobiera wszystkich klientów agenta jednym zapytaniem zamiast kilku po kolei –
na serwerze w internecie każde zapytanie to dodatkowe opóźnienie.
"""

from rest_framework.pagination import PageNumberPagination


class CrmPagination(PageNumberPagination):
    page_size_query_param = "page_size"
    max_page_size = 500
