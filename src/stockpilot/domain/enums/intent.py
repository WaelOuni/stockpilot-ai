from enum import Enum


class Intent(str, Enum):
    PRODUCT_ADVICE = "PRODUCT_ADVICE"
    STOCK_CHECK = "STOCK_CHECK"
    ORDER = "ORDER"
    ORDER_STATUS = "ORDER_STATUS"
    UNKNOWN = "UNKNOWN"
