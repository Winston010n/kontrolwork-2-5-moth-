import hashlib

from django.core.cache import cache
from django.db import transaction
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.generics import CreateAPIView, RetrieveUpdateAPIView
from rest_framework.permissions import AllowAny, IsAdminUser, IsAuthenticated
from rest_framework.response import Response

from .models import Booking, Profile, Room
from .serializers import (
    BookingCreateSerializer,
    BookingSerializer,
    ProfileSerializer,
    RegisterSerializer,
    RoomSerializer,
)


class RegisterView(CreateAPIView):
    serializer_class = RegisterSerializer
    permission_classes = [AllowAny]


class ProfileView(RetrieveUpdateAPIView):
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    def get_object(self):
        profile, _ = Profile.objects.get_or_create(user=self.request.user)
        return profile


@extend_schema_view(
    list=extend_schema(description="Публичный список доступных комнат. Результат кэшируется на 60 секунд."),
    create=extend_schema(description="Создание комнаты; только для администратора."),
)
class RoomViewSet(viewsets.ModelViewSet):
    serializer_class = RoomSerializer
    filterset_fields = {"capacity": ["exact", "gte", "lte"], "hourly_rate": ["exact", "gte", "lte"]}
    search_fields = ["name"]
    ordering_fields = ["hourly_rate", "capacity", "name"]
    ordering = ["name"]

    def get_queryset(self):
        return Room.objects.all() if self.request.user.is_staff else Room.objects.filter(is_available=True)

    def get_permissions(self):
        if self.action in ("create", "update", "partial_update", "destroy"):
            return [IsAdminUser()]
        return [AllowAny()]

    def list(self, request, *args, **kwargs):
        version = cache.get("rooms:list:version", 1)
        digest = hashlib.sha256(request.get_full_path().encode("utf-8")).hexdigest()
        visibility = "staff" if request.user.is_staff else "public"
        key = f"rooms:list:{visibility}:v{version}:{digest}"
        cached_data = cache.get(key)
        if cached_data is not None:
            return Response(cached_data)
        response = super().list(request, *args, **kwargs)
        cache.set(key, response.data, 60)
        return response

    @transaction.atomic
    def perform_destroy(self, instance):
        room = Room.objects.select_for_update().get(pk=instance.pk)
        if room.bookings.exists():
            raise ValidationError({"detail": "Нельзя удалить комнату, для которой сохранена история бронирований."})
        room.delete()


class BookingViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = Booking.objects.none()
    permission_classes = [IsAuthenticated]
    filterset_fields = ["status"]
    ordering_fields = ["start_time", "end_time", "created_at"]
    ordering = ["-start_time"]

    def get_queryset(self):
        return Booking.objects.filter(user=self.request.user).select_related("room", "user")

    def get_serializer_class(self):
        return BookingCreateSerializer if self.action == "create" else BookingSerializer

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        booking = serializer.save()
        return Response(BookingSerializer(booking).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["post"])
    @transaction.atomic
    def cancel(self, request, pk=None):
        booking = self.get_queryset().select_for_update().get(pk=pk)
        if booking.status == Booking.Status.CANCELLED:
            return Response({"detail": "Бронирование уже отменено."}, status=status.HTTP_400_BAD_REQUEST)
        booking.status = Booking.Status.CANCELLED
        booking.save(update_fields=["status"])
        profile = Profile.objects.select_for_update().get(user=request.user)
        profile.balance += booking.total_price
        profile.save(update_fields=["balance"])
        return Response(BookingSerializer(booking).data)

    @action(detail=True, methods=["post"], permission_classes=[IsAdminUser])
    @transaction.atomic
    def confirm(self, request, pk=None):
        booking = self.get_queryset_for_admin().select_for_update().filter(pk=pk).first()
        if booking is None:
            return Response({"detail": "Бронирование не найдено."}, status=status.HTTP_404_NOT_FOUND)
        if booking.status != Booking.Status.PENDING:
            return Response({"detail": "Подтвердить можно только ожидающее бронирование."}, status=status.HTTP_400_BAD_REQUEST)
        booking.status = Booking.Status.CONFIRMED
        booking.save(update_fields=["status"])
        return Response(BookingSerializer(booking).data)

    def get_queryset_for_admin(self):
        return Booking.objects.select_related("room", "user")
