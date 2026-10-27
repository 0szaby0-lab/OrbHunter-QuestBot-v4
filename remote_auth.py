# -*- coding: utf-8 -*-
"""
Discord Remote Auth (QR-Kódos hitelesítés) modul.
Lehetővé teszi a felhasználóknak, hogy a Discord mobil app QR-kód beolvasójával
jelentkezzenek be a küldetések automatikus futtatásához, token kézi másolása nélkül.
"""

import asyncio
import base64
import io
import json
from typing import Callable, Optional, Dict, Any

import aiohttp
import qrcode
from Cryptodome.PublicKey import RSA
from Cryptodome.Cipher import PKCS1_OAEP
from Cryptodome.Hash import SHA256

from logger import log

REMOTE_AUTH_WS = "wss://remote-auth-gateway.discord.gg/?v=2"
LOGIN_TICKET_URL = "https://discord.com/api/v9/users/@me/remote-auth/login"


class RemoteAuthHandler:
    """Kezeli a Discord Remote Auth v2 WebSocket kapcsolatot és a kulcscserét."""

    def __init__(
        self,
        on_qr_code: Optional[Callable[[bytes, str], Any]] = None,
        on_scanned: Optional[Callable[[Dict[str, str]], Any]] = None,
        on_token: Optional[Callable[[str], Any]] = None,
        on_error: Optional[Callable[[str], Any]] = None,
        on_cancel: Optional[Callable[[], Any]] = None,
    ):
        self.on_qr_code = on_qr_code
        self.on_scanned = on_scanned
        self.on_token = on_token
        self.on_error = on_error
        self.on_cancel = on_cancel

        self.key = RSA.generate(2048)
        self.cipher = PKCS1_OAEP.new(self.key, hashAlgo=SHA256)
        pub_pem = self.key.publickey().export_key(format="PEM").decode("utf-8")
        self.encoded_public_key = "".join(pub_pem.split("\n")[1:-1])

        self.cancelled = False
        self._heartbeat_task: Optional[asyncio.Task] = None
        self._ws = None

    def _decrypt(self, encrypted_b64: str) -> bytes:
        return self.cipher.decrypt(base64.b64decode(encrypted_b64))

    def generate_qr_image(self, url: str) -> bytes:
        """QR kód PNG bájtjainak generálása a Discord URL-ből."""
        qr = qrcode.QRCode(version=1, box_size=8, border=2)
        qr.add_data(url)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        return buf.getvalue()

    async def _heartbeat_loop(self, interval: float):
        try:
            while not self.cancelled and self._ws and not self._ws.closed:
                await asyncio.sleep(interval)
                await self._ws.send_json({"op": "heartbeat"})
        except asyncio.CancelledError:
            pass
        except Exception as e:
            log(f"Remote Auth Heartbeat hiba: {e}", "warn")

    async def _exchange_ticket(self, session: aiohttp.ClientSession, ticket: str) -> Optional[str]:
        headers = {
            "Origin": "https://discord.com",
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        }
        async with session.post(LOGIN_TICKET_URL, json={"ticket": ticket}, headers=headers) as resp:
            if resp.status == 200:
                data = await resp.json()
                encrypted_token = data.get("encrypted_token")
                if encrypted_token:
                    token = self._decrypt(encrypted_token).decode("utf-8")
                    return token
                else:
                    log("A remote-auth válaszban nem található encrypted_token", "error")
            else:
                body = await resp.text()
                log(f"Hiba a ticket beváltásakor: status {resp.status}, válasz: {body}", "error")
        return None

    def cancel(self):
        """Munkamenet leállítása kívülről."""
        self.cancelled = True
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        if self._ws and not self._ws.closed:
            asyncio.create_task(self._ws.close())

    async def run(self, timeout_seconds: int = 120):
        """Elindítja a WebSocket kapcsolatot és a hitelesítési folyamatot."""
        headers = {"Origin": "https://discord.com"}

        try:
            async with aiohttp.ClientSession() as http_session:
                async with http_session.ws_connect(REMOTE_AUTH_WS, headers=headers) as ws:
                    self._ws = ws

                    async def handle_messages():
                        async for msg in ws:
                            if self.cancelled:
                                break
                            if msg.type == aiohttp.WSMsgType.TEXT:
                                data = json.loads(msg.data)
                                op = data.get("op")

                                if op == "hello":
                                    heartbeat_interval = data.get("heartbeat_interval", 41250) / 1000.0
                                    self._heartbeat_task = asyncio.create_task(self._heartbeat_loop(heartbeat_interval))
                                    await ws.send_json({"op": "init", "encoded_public_key": self.encoded_public_key})

                                elif op == "nonce_proof":
                                    decrypted = self._decrypt(data["encrypted_nonce"])
                                    proof_hash = SHA256.new(data=decrypted).digest()
                                    proof_b64 = base64.urlsafe_b64encode(proof_hash).rstrip(b"=").decode("utf-8")
                                    await ws.send_json({"op": "nonce_proof", "proof": proof_b64})

                                elif op == "pending_remote_init":
                                    fingerprint = data.get("fingerprint")
                                    qr_url = f"https://discord.com/ra/{fingerprint}"
                                    qr_bytes = self.generate_qr_image(qr_url)
                                    if self.on_qr_code:
                                        res = self.on_qr_code(qr_bytes, qr_url)
                                        if asyncio.iscoroutine(res):
                                            await res

                                elif op == "pending_ticket":
                                    enc_user = data.get("encrypted_user_payload")
                                    user_info = {}
                                    try:
                                        dec_user = self._decrypt(enc_user).decode("utf-8")
                                        parts = dec_user.split(":")
                                        if len(parts) >= 4:
                                            user_info = {
                                                "user_id": parts[0],
                                                "discriminator": parts[1],
                                                "avatar": parts[2],
                                                "username": parts[3],
                                            }
                                        else:
                                            user_info = {"raw": dec_user}
                                    except Exception as ex:
                                        log(f"Nem sikerült dekódolni a felhasználói adatokat: {ex}", "warn")

                                    if self.on_scanned:
                                        res = self.on_scanned(user_info)
                                        if asyncio.iscoroutine(res):
                                            await res

                                elif op == "pending_login":
                                    ticket = data.get("ticket")
                                    token = await self._exchange_ticket(http_session, ticket)
                                    if token:
                                        if self.on_token:
                                            res = self.on_token(token)
                                            if asyncio.iscoroutine(res):
                                                await res
                                    else:
                                        if self.on_error:
                                            res = self.on_error("Nem sikerült lekérni a Discord munkamenet tokent.")
                                            if asyncio.iscoroutine(res):
                                                await res
                                    break

                                elif op == "cancel":
                                    if self.on_cancel:
                                        res = self.on_cancel()
                                        if asyncio.iscoroutine(res):
                                            await res
                                    break

                            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                                break

                    try:
                        await asyncio.wait_for(handle_messages(), timeout=timeout_seconds)
                    except asyncio.TimeoutError:
                        if not self.cancelled:
                            if self.on_error:
                                res = self.on_error("A QR-kód érvényessége lejárt (időtúllépés).")
                                if asyncio.iscoroutine(res):
                                    await res
        except Exception as e:
            if not self.cancelled:
                log(f"Hiba a Remote Auth futása közben: {e}", "error")
                if self.on_error:
                    res = self.on_error(f"Hiba a hitelesítés során: {e}")
                    if asyncio.iscoroutine(res):
                        await res
        finally:
            if self._heartbeat_task:
                self._heartbeat_task.cancel()
