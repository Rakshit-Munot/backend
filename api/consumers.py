import json
from channels.generic.websocket import AsyncWebsocketConsumer

class FileConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("file_updates", self.channel_name)
        print("WebSocket connected:", self.scope['path'])
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("file_updates", self.channel_name)

    async def send_file_update(self, event):
        await self.send(text_data=json.dumps(event["data"]))


class UserConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("user_updates", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("user_updates", self.channel_name)

    async def send_user_update(self, event):
        await self.send(text_data=json.dumps(event["data"]))


class InstrumentConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("instrument_updates", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("instrument_updates", self.channel_name)

    async def send_instrument_update(self, event):
        await self.send(text_data=json.dumps(event["data"]))


class IssueRequestConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("issue_request_updates", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("issue_request_updates", self.channel_name)

    async def send_issue_update(self, event):
        await self.send(text_data=json.dumps(event["data"]))


class BillConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("bill_updates", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("bill_updates", self.channel_name)

    async def send_bill_update(self, event):
        await self.send(text_data=json.dumps(event["data"]))


class HandoutConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("handout_updates", self.channel_name)
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("handout_updates", self.channel_name)

    async def send_handout_update(self, event):
        await self.send(text_data=json.dumps(event["data"]))
