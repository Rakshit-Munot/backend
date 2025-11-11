from django.urls import path
from . import consumers

websocket_urlpatterns = [
    path("ws/files/", consumers.FileConsumer.as_asgi()),
    path("ws/users/", consumers.UserConsumer.as_asgi()),
    path("ws/instruments/", consumers.InstrumentConsumer.as_asgi()),
    path("ws/issue-requests/", consumers.IssueRequestConsumer.as_asgi()),
    path("ws/bills/", consumers.BillConsumer.as_asgi()),
]
