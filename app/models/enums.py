from enum import Enum


class UserRole(str, Enum):
    user = "user"
    admin = "admin"


class OrderStatus(str, Enum):
    pending = "pending"
    accepted = "accepted"
    out_for_delivery = "out_for_delivery"
    delivered = "delivered"
