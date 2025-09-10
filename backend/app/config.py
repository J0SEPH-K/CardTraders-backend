import os
from typing import Any, Dict, Optional

_SERVER_CONFIG: Dict[str, Any] = {}


def get_server_secret(key: str, default: Optional[Any] = None) -> Any:
    """Read a server-side secret. Precedence: loaded Mongo config -> environment -> default.
    Do not expose these to clients.
    """
    if key in _SERVER_CONFIG:
        return _SERVER_CONFIG[key]
    return os.getenv(key, default)  # type: ignore[no-any-return]


def _allowlisted_public_from_env() -> Dict[str, Any]:
    """Expose only safe, intentionally public values from env.
    Keys beginning with EXPO_PUBLIC_ are considered safe to ship to clients.
    """
    out: Dict[str, Any] = {}
    for k, v in os.environ.items():
        if k.startswith("EXPO_PUBLIC_"):
            out[k] = v
    return out


async def load_server_config_from_mongo(mdb) -> None:
    """Load server config from MongoDB into memory if available.
    The expected document shape (collection: config, id: 'runtime'):
      { _id: 'runtime', server: { KEY: VALUE, ... }, public: { EXPO_PUBLIC_*: VALUE, ... } }
    """
    global _SERVER_CONFIG
    if mdb is None:
        return
    coll = mdb.get_collection("config")
    doc = await coll.find_one({"_id": "runtime"})
    if not doc:
        return
    server = doc.get("server") or {}
    if isinstance(server, dict):
        # Merge into memory; prefer Mongo values
        _SERVER_CONFIG.update(server)


async def get_public_config(mdb) -> Dict[str, Any]:
    """Return public configuration for clients. Combines Mongo 'public' map and EXPO_PUBLIC_* envs."""
    public: Dict[str, Any] = {}
    if mdb is not None:
        coll = mdb.get_collection("config")
        doc = await coll.find_one({"_id": "runtime"})
        if doc and isinstance(doc.get("public"), dict):
            public.update(doc["public"])  # type: ignore[index]
    # Env wins as an override
    public.update(_allowlisted_public_from_env())
    return public
