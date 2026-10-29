"""Exercise the actual Discord RTP cryptography with patched PyNaCl."""
import unittest
from types import SimpleNamespace
import discord
import discord.voice_client
import discord.voice_state
import nacl.secret


class VoiceCryptoCompatibilityTest(unittest.TestCase):
    def test_rtp_aead_round_trip_and_header_authentication(self):
        self.assertTrue(discord.voice_client.has_nacl)
        self.assertTrue(discord.voice_state.has_dave)
        client = discord.VoiceClient.__new__(discord.VoiceClient)
        client._connection = SimpleNamespace(secret_key=list(range(32)))
        client._incr_nonce = 1
        header = bytes([0x80, 0x78]) + bytes(10)
        payload = b"offline-opus-compatibility"
        encrypted = client._encrypt_aead_xchacha20_poly1305_rtpsize(header, payload)
        nonce = encrypted[-4:] + bytes(20)
        box = nacl.secret.Aead(bytes(client.secret_key))
        self.assertEqual(box.decrypt(encrypted[12:-4], header, nonce), payload)
        self.assertEqual(client._incr_nonce, 2)
        with self.assertRaises(nacl.exceptions.CryptoError):
            box.decrypt(encrypted[12:-4], bytes(12), nonce)
