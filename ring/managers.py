import datetime
import json
from datetime import timezone

import aiohttp
from asgiref.sync import sync_to_async
from django.db import models
from django.utils import timezone as dj_timezone
from rest_framework.exceptions import ValidationError
from ring_doorbell import Auth, AuthenticationError, Requires2FAError

from ring.tasks import set_ring_token


class RingAccountManager(models.Manager):
    user_agent = "Jupyter"
    accounts = {}

    async def load_account_async(self):
        self.account = await sync_to_async(self.model.objects.first)()
        self.token = json.loads(self.account.token) if self.account else None
        return self.account

    # --- SYNC CALLBACK FOR RING ---
    def update_token(self, username, token):
        print(username, json.dumps(token))
        self.accounts[username] = json.dumps(token)

    async def authenticate(self, username, password, auth_code=None):
        auth = Auth(
            self.user_agent,
            None,
            lambda token: self.update_token(username, token),  # SYNC CALLBACK
        )

        try:
            if auth_code:
                await auth.async_fetch_token(username, password, auth_code)
            else:
                await auth.async_fetch_token(username, password)
        except Requires2FAError:
            raise ValidationError({"error": "2FA code is required"})
        except AuthenticationError:
            raise ValidationError({"error": "Invalid Ring credentials"})
        account, _ = await sync_to_async(self.model.objects.update_or_create)(
            username=username,
            defaults={
                "token": self.accounts[username],
            },
            create_defaults={
                "username": username,
                "token": self.accounts[username],
            },
        )

        # After login, self.token has been updated via callback
        account_data = self.accounts[username]
        # Parse the JSON string to a dictionary
        account_dict = json.loads(account_data)
        # Extract the refresh_token
        refresh_token = account_dict.get("refresh_token")
        set_ring_token.apply_async(args=(refresh_token,), queue="camera_queue")
        # set_ring_token.apply_async(args=(refresh_token,), queue="camera_queue")

        return account

    def is_expired(self):
        if not self.token:
            return True

        expires_at = self.token.get("expires_at")
        if not expires_at:
            return True

        if isinstance(expires_at, (int, float)):
            expires_at = datetime.datetime.fromtimestamp(expires_at, tz=timezone.utc)

        elif isinstance(expires_at, str):
            expires_at = dj_timezone.make_aware(
                datetime.datetime.fromisoformat(expires_at)
            )

        now = dj_timezone.now()

        return now >= expires_at - datetime.timedelta(seconds=30)

    async def refresh(self):
        if not self.token:
            await self.load_account_async()

        url = "https://oauth.ring.com/oauth/token"
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self.token["refresh_token"],
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 400:
                    raise RingAuthExpired("Refresh token expired")

                data = await resp.json()

        # Update token
        self.token["access_token"] = data["access_token"]
        self.token["refresh_token"] = data.get(
            "refresh_token", self.token["refresh_token"]
        )

        # Save
        self.account.token = json.dumps(self.token)
        self.account.expires_at = dj_timezone.now() + datetime.timedelta(
            seconds=data.get("expires_in", 3600)
        )
        await sync_to_async(self.account.save)()

    async def get(self, url):
        await self.load_account_async()

        if self.is_expired():
            await self.refresh()

        headers = {
            "Authorization": f"Bearer {self.token['access_token']}",
            "Content-Type": "application/json",
        }

        async with aiohttp.ClientSession() as session:
            async with session.get(url, headers=headers) as resp:
                if resp.status == 401:
                    # retry once
                    await self.refresh()
                    headers["Authorization"] = f"Bearer {self.token['access_token']}"
                    async with session.get(url, headers=headers) as retry:
                        return await retry.json()
                return await resp.json()

    async def get_devices(self):
        data = await self.get("https://api.ring.com/clients_api/ring_devices")
        cameras = data.get("doorbots", []) + data.get("stickup_cams", [])
        return [
            {
                "name": cam.get("description"),
                "ring_id": cam.get("id"),
                "ring_device_id": cam.get("device_id"),
            }
            for cam in cameras
        ]


class RingAuthExpired(Exception):
    pass
