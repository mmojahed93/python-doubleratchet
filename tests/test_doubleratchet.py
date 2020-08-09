import base64
import json
import os

import pytest
from nacl.bindings.crypto_scalarmult import crypto_scalarmult
from nacl.public import PrivateKey as Curve25519DecryptionKey
from nacl.public import PublicKey  as Curve25519EncryptionKey

import doubleratchet
from doubleratchet.exceptions import MissingKeyException


class SendReceiveChain(doubleratchet.kdfchains.ConstKDFChain):
    def __init__(self, key):
        super(SendReceiveChain, self).__init__(
            "const_data".encode("US-ASCII"),
            doubleratchet.recommended.RootKeyKDF(
                "SHA-512",
                "RootKeyKDF info string".encode("US-ASCII")
            ),
            key
        )


class SymmetricKeyRatchet(doubleratchet.ratchets.SymmetricKeyRatchet):
    def __init__(self):
        super(SymmetricKeyRatchet, self).__init__(
            SendReceiveChain,
            SendReceiveChain
        )


class RootChain(doubleratchet.kdfchains.KDFChain):
    def __init__(self):
        super(RootChain, self).__init__(
            doubleratchet.recommended.RootKeyKDF(
                "SHA-512",
                "IAmARootChain".encode("US-ASCII")
            ),
            "I am a root key!".encode("US-ASCII")
        )


class KeyPair(doubleratchet.KeyPair):
    def __init__(self, priv=None, pub=None):
        wrap = self.__class__.__wrap

        self.__priv = wrap(priv, Curve25519DecryptionKey)
        self.__pub = wrap(pub, Curve25519EncryptionKey)

        if not self.__priv == None and self.__pub == None:
            self.__pub = self.__priv.public_key

    @classmethod
    def generate(cls):
        return cls(priv=Curve25519DecryptionKey.generate())

    @staticmethod
    def __wrap(key, cls):
        if key == None:
            return None

        if isinstance(key, cls):
            return key

        return cls(key)

    def serialize(self):
        pub = self.pub
        pub = None if pub == None else base64.b64encode(bytes(pub)).decode("US-ASCII")

        priv = self.priv
        priv = None if priv == None else base64.b64encode(bytes(priv)).decode("US-ASCII")

        return {
            "super": super(KeyPair, self).serialize(),
            "pub": pub,
            "priv": priv
        }

    @classmethod
    def fromSerialized(cls, serialized, *args, **kwargs):
        self = super(KeyPair, cls).fromSerialized(
            serialized["super"],
            *args,
            **kwargs
        )

        if serialized["pub"] != None:
            self.__pub = cls.__wrap(
                base64.b64decode(serialized["pub"].encode("US-ASCII")),
                Curve25519EncryptionKey
            )

        if serialized["priv"] != None:
            self.__priv = cls.__wrap(
                base64.b64decode(serialized["priv"].encode("US-ASCII")),
                Curve25519DecryptionKey
            )

        return self

    @property
    def pub(self):
        return None if self.__pub == None else bytes(self.__pub)

    @property
    def priv(self):
        return None if self.__priv == None else bytes(self.__priv)

    def getSharedSecret(self, other):
        if self.__priv == None:
            raise MissingKeyException(
                "Cannot get a shared secret using this KeyPair, private key missing."
            )

        if other.__pub == None:
            raise MissingKeyException(
                "Cannot get a shared secret using the other KeyPair, public key missing."
            )

        return crypto_scalarmult(
            self.priv,
            other.pub
        )


class DR(doubleratchet.ratchets.DoubleRatchet):
    def __init__(self, own_key=None, other_pub=None):
        super(DR, self).__init__(
            doubleratchet.recommended.CBCHMACAEAD(
                "SHA-512",
                "ExampleCBCHMACAEADConfig".encode("US-ASCII")
            ),
            5,
            SymmetricKeyRatchet(),
            "some associated data".encode("US-ASCII"),
            KeyPair,
            RootChain(),
            own_key,
            other_pub
        )

    def _makeAD(self, header, ad):
        return ad


def test_messages():
    alice_key = KeyPair.generate()

    alice_ratchet = DR(own_key=alice_key)
    bob_ratchet = DR(other_pub=alice_key.pub)

    assert not alice_ratchet.canSend()
    assert bob_ratchet.canSend()

    for _ in range(100):
        message = os.urandom(100)

        c = bob_ratchet.encryptMessage(message)

        assert alice_ratchet.decryptMessage(c["ciphertext"], c["header"]) == message

        message = os.urandom(100)

        c = alice_ratchet.encryptMessage(message)

        assert bob_ratchet.decryptMessage(c["ciphertext"], c["header"]) == message

    assert alice_ratchet.canSend()
    assert bob_ratchet.canSend()


def test_not_synced():
    alice_key = KeyPair.generate()
    alice_ratchet = DR(own_key=alice_key)

    with pytest.raises(doubleratchet.exceptions.NotInitializedException):
        alice_ratchet.encryptMessage("I will fail!".encode("US-ASCII"))


def test_skipped_message():
    alice_key = KeyPair.generate()

    alice_ratchet = DR(own_key=alice_key)
    bob_ratchet = DR(other_pub=alice_key.pub)

    for _ in range(100):
        message_a = os.urandom(100)
        message_b = os.urandom(100)

        c_a = bob_ratchet.encryptMessage(message_a)
        c_b = bob_ratchet.encryptMessage(message_b)

        assert alice_ratchet.decryptMessage(c_b["ciphertext"], c_b["header"]) == message_b
        assert alice_ratchet.decryptMessage(c_a["ciphertext"], c_a["header"]) == message_a

        message_a = os.urandom(100)
        message_b = os.urandom(100)

        c_a = alice_ratchet.encryptMessage(message_a)
        c_b = alice_ratchet.encryptMessage(message_b)

        assert bob_ratchet.decryptMessage(c_b["ciphertext"], c_b["header"]) == message_b
        assert bob_ratchet.decryptMessage(c_a["ciphertext"], c_a["header"]) == message_a


def test_too_many_skipped_messages():
    alice_key = KeyPair.generate()

    alice_ratchet = DR(own_key=alice_key)
    bob_ratchet = DR(other_pub=alice_key.pub)

    # Skip six messages (five skipped messages are allowed)
    for _ in range(6):
        bob_ratchet.encryptMessage(os.urandom(100))

    # Encrypt a seventh message
    c = bob_ratchet.encryptMessage(os.urandom(100))

    with pytest.raises(doubleratchet.exceptions.TooManySavedMessageKeysException):
        alice_ratchet.decryptMessage(c["ciphertext"], c["header"])


def test_serialization():
    alice_key = KeyPair.generate()

    alice_ratchet = DR(own_key=alice_key)
    bob_ratchet = DR(other_pub=alice_key.pub)

    for _ in range(100):
        message = os.urandom(100)

        c = bob_ratchet.encryptMessage(message)

        assert alice_ratchet.decryptMessage(c["ciphertext"], c["header"]) == message

        message = os.urandom(100)

        c = alice_ratchet.encryptMessage(message)

        assert bob_ratchet.decryptMessage(c["ciphertext"], c["header"]) == message

    alice_ratchet_serialized = json.dumps(alice_ratchet.serialize())
    bob_ratchet_serialized = json.dumps(bob_ratchet.serialize())

    print(alice_ratchet_serialized)
    print(bob_ratchet_serialized)

    alice_ratchet = DR.fromSerialized(json.loads(alice_ratchet_serialized))
    bob_ratchet = DR.fromSerialized(json.loads(bob_ratchet_serialized))

    for _ in range(100):
        message = os.urandom(100)

        c = bob_ratchet.encryptMessage(message)

        assert alice_ratchet.decryptMessage(c["ciphertext"], c["header"]) == message

        message = os.urandom(100)

        c = alice_ratchet.encryptMessage(message)

        assert bob_ratchet.decryptMessage(c["ciphertext"], c["header"]) == message
