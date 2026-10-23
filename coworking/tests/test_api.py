from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from coworking.models import Booking, Profile, Room

User = get_user_model()


class SmartCoworkingAPITests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="alice", password="Passw0rd!abc")
        self.profile = Profile.objects.create(user=self.user, phone="+996700000000", balance=Decimal("500.00"))
        self.room = Room.objects.create(name="Focus room", capacity=4, hourly_rate=Decimal("100.00"))
        self.client.force_authenticate(self.user)
        self.start = (timezone.now() + timedelta(days=1)).replace(minute=0, second=0, microsecond=0)

    def payload(self, start=None, end=None, room=None):
        start = start or self.start
        end = end or start + timedelta(hours=2)
        return {"room": room or self.room.pk, "start_time": start.isoformat(), "end_time": end.isoformat()}

    def test_booking_charges_balance_and_calculates_price(self):
        response = self.client.post("/api/bookings/", self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED, response.data)
        self.assertEqual(Decimal(response.data["total_price"]), Decimal("200.00"))
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.balance, Decimal("300.00"))

    def test_overlapping_active_booking_is_rejected_but_adjacent_is_allowed(self):
        self.client.post("/api/bookings/", self.payload(), format="json")
        overlap = self.client.post("/api/bookings/", self.payload(self.start + timedelta(hours=1)), format="json")
        adjacent = self.client.post("/api/bookings/", self.payload(self.start + timedelta(hours=2)), format="json")
        self.assertEqual(overlap.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(adjacent.status_code, status.HTTP_201_CREATED, adjacent.data)

    def test_cancel_refunds_and_releases_slot(self):
        created = self.client.post("/api/bookings/", self.payload(), format="json")
        booking_id = created.data["id"]
        response = self.client.post(f"/api/bookings/{booking_id}/cancel/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.balance, Decimal("500.00"))
        again = self.client.post("/api/bookings/", self.payload(), format="json")
        self.assertEqual(again.status_code, status.HTTP_201_CREATED, again.data)

    def test_user_cannot_read_another_users_booking(self):
        booking = Booking.objects.create(
            user=self.user,
            room=self.room,
            start_time=self.start,
            end_time=self.start + timedelta(hours=1),
            total_price=Decimal("100.00"),
        )
        other = User.objects.create_user(username="bob", password="Passw0rd!abc")
        self.client.force_authenticate(other)
        response = self.client.get(f"/api/bookings/{booking.pk}/")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_balance_is_read_only_and_room_delete_preserves_booking_history(self):
        response = self.client.patch("/api/profile/", {"balance": "99999.00", "phone": "+996711111111"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.balance, Decimal("500.00"))
        Booking.objects.create(
            user=self.user,
            room=self.room,
            start_time=self.start,
            end_time=self.start + timedelta(hours=1),
            total_price=Decimal("100.00"),
        )
        denied = self.client.delete(f"/api/rooms/{self.room.pk}/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)
        admin = User.objects.create_superuser(username="admin", password="AdminPass123!", email="admin@example.com")
        self.client.force_authenticate(admin)
        response = self.client.delete(f"/api/rooms/{self.room.pk}/")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_insufficient_balance_and_admin_confirmation(self):
        self.profile.balance = Decimal("50.00")
        self.profile.save(update_fields=["balance"])
        response = self.client.post("/api/bookings/", self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.profile.balance = Decimal("500.00")
        self.profile.save(update_fields=["balance"])
        response = self.client.post("/api/bookings/", self.payload(), format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        booking_id = response.data["id"]
        admin = User.objects.create_superuser(username="admin", password="AdminPass123!", email="admin@example.com")
        self.client.force_authenticate(admin)
        response = self.client.post(f"/api/bookings/{booking_id}/confirm/")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], Booking.Status.CONFIRMED)

    def test_public_room_list_search_and_filter(self):
        self.client.force_authenticate(user=None)
        response = self.client.get("/api/rooms/?search=Focus&capacity__gte=2&ordering=hourly_rate")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["count"], 1)

    def test_room_list_cache_is_separated_for_staff(self):
        Room.objects.create(name="Private room", capacity=2, hourly_rate=Decimal("50.00"), is_available=False)
        self.client.force_authenticate(user=None)
        public_response = self.client.get("/api/rooms/")
        admin = User.objects.create_superuser(username="admin", password="AdminPass123!", email="admin@example.com")
        self.client.force_authenticate(admin)
        staff_response = self.client.get("/api/rooms/")

        self.assertEqual(public_response.data["count"], 1)
        self.assertEqual(staff_response.data["count"], 2)

    def test_inactive_room_and_invalid_duration_are_rejected(self):
        self.room.is_available = False
        self.room.save()
        unavailable = self.client.post("/api/bookings/", self.payload(), format="json")
        self.assertEqual(unavailable.status_code, status.HTTP_400_BAD_REQUEST)
        self.room.is_available = True
        self.room.save()
        too_short = self.client.post("/api/bookings/", self.payload(end=self.start + timedelta(minutes=30)), format="json")
        self.assertEqual(too_short.status_code, status.HTTP_400_BAD_REQUEST)
