from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP

from django.contrib.auth import get_user_model, password_validation
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers
from rest_framework.exceptions import ValidationError

from .models import Booking, Profile, Room

User = get_user_model()


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8, style={"input_type": "password"})
    phone = serializers.CharField(write_only=True, required=False, allow_blank=True, max_length=32)

    class Meta:
        model = User
        fields = ("id", "username", "email", "password", "phone")
        read_only_fields = ("id",)

    def validate_password(self, value):
        password_validation.validate_password(value)
        return value

    @transaction.atomic
    def create(self, validated_data):
        phone = validated_data.pop("phone", "")
        password = validated_data.pop("password")
        user = User(**validated_data)
        user.set_password(password)
        user.save()
        Profile.objects.create(user=user, phone=phone)
        return user


class ProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Profile
        fields = ("username", "phone", "balance")
        read_only_fields = ("username", "balance")


class RoomSerializer(serializers.ModelSerializer):
    class Meta:
        model = Room
        fields = ("id", "name", "description", "capacity", "hourly_rate", "is_available", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")


class BookingCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = ("id", "room", "start_time", "end_time")
        read_only_fields = ("id",)

    def validate(self, attrs):
        start, end, room = attrs["start_time"], attrs["end_time"], attrs["room"]
        if end <= start:
            raise ValidationError({"end_time": "Время окончания должно быть позже времени начала."})
        duration = end - start
        if duration < timedelta(hours=1) or duration > timedelta(hours=12):
            raise ValidationError({"end_time": "Продолжительность бронирования должна быть от 1 до 12 часов."})
        if start <= timezone.now():
            raise ValidationError({"start_time": "Нельзя бронировать прошедшее время."})
        if not room.is_available:
            raise ValidationError({"room": "Эта комната сейчас недоступна для бронирования."})
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        user = self.context["request"].user
        room_id = validated_data["room"].pk
        room = Room.objects.select_for_update().get(pk=room_id)
        if not room.is_available:
            raise ValidationError({"room": "Эта комната сейчас недоступна для бронирования."})

        start, end = validated_data["start_time"], validated_data["end_time"]
        conflict = Booking.objects.filter(
            room=room,
            status__in=(Booking.Status.PENDING, Booking.Status.CONFIRMED),
            start_time__lt=end,
            end_time__gt=start,
        ).exists()
        if conflict:
            raise ValidationError({"non_field_errors": ["Выбранное время пересекается с другим активным бронированием."]})

        profile, _ = Profile.objects.select_for_update().get_or_create(user=user)
        hours = Decimal(str((end - start).total_seconds())) / Decimal("3600")
        price = (room.hourly_rate * hours).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if profile.balance < price:
            raise ValidationError({"balance": f"Недостаточно средств. Стоимость бронирования: {price}."})
        profile.balance -= price
        profile.save(update_fields=["balance"])
        return Booking.objects.create(
            user=user,
            room=room,
            start_time=start,
            end_time=end,
            total_price=price,
        )


class BookingSerializer(serializers.ModelSerializer):
    room_name = serializers.CharField(source="room.name", read_only=True)
    username = serializers.CharField(source="user.username", read_only=True)

    class Meta:
        model = Booking
        fields = ("id", "username", "room", "room_name", "start_time", "end_time", "total_price", "status", "created_at")
        read_only_fields = fields
