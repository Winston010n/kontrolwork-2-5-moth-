from django.core.cache import cache
from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Room


def bump_room_list_version():
    key = "rooms:list:version"
    if not cache.add(key, 1, timeout=None):
        try:
            cache.incr(key)
        except ValueError:
            cache.set(key, 1, timeout=None)


@receiver([post_save, post_delete], sender=Room)
def invalidate_room_list_cache(**kwargs):
    bump_room_list_version()
