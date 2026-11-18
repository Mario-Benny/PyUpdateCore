"""Detached-in-manifest Ed25519 verification; private keys never enter this package."""
from __future__ import annotations

import base64
import json
from typing import Any, Mapping

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .errors import ManifestError


def canonical_manifest_bytes(document: Mapping[str, Any]) -> bytes:
    unsigned = {key: value for key, value in document.items() if key != "signature"}
    return json.dumps(unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def verify_manifest_signature(document: Mapping[str, Any], trusted_keys: Mapping[str, str]) -> str:
    signature = document.get("signature")
    if not isinstance(signature, dict) or signature.get("algorithm") != "ed25519":
        raise ManifestError("更新清单缺少 Ed25519 签名")
    key_id, value = signature.get("key_id"), signature.get("value")
    if not isinstance(key_id, str) or not isinstance(value, str) or key_id not in trusted_keys:
        raise ManifestError("更新清单签名密钥不受信任")
    try:
        public_key = Ed25519PublicKey.from_public_bytes(base64.b64decode(trusted_keys[key_id], validate=True))
        encoded_signature = base64.b64decode(value, validate=True)
        public_key.verify(encoded_signature, canonical_manifest_bytes(document))
    except (ValueError, InvalidSignature) as exc:
        raise ManifestError("更新清单 Ed25519 签名验证失败") from exc
    return key_id
