from django.urls import path
from . import consumers

websocket_urlpatterns = [
    path("ws/files/", consumers.FileConsumer.as_asgi()),
    path("ws/users/", consumers.UserConsumer.as_asgi()),
]
