from django.contrib import admin

from .models import Booking, Profile, Room


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ("name", "capacity", "hourly_rate", "is_available")
    list_filter = ("is_available",)
    search_fields = ("name",)


@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ("user", "phone", "balance")
    search_fields = ("user__username", "phone")


@admin.register(Booking)
class BookingAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "room", "start_time", "end_time", "total_price", "status")
    list_filter = ("status",)
    search_fields = ("user__username", "room__name")
    readonly_fields = ("user", "room", "start_time", "end_time", "total_price", "created_at")
