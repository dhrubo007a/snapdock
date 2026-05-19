"""Shared SlowAPI rate-limiter instance.

Import `limiter` in main.py to attach it to the app, and in route modules
to apply @limiter.limit() decorators.
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
