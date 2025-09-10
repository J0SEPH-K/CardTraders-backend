# CardTraders Backend
FastAPI service under `/backend`.

## Runtime Config via MongoDB

The backend can load server-only config from Mongo and expose public config to clients:

- Collection: `config`, Document id: `runtime`
- Shape: `{ _id: 'runtime', server: { ...private keys... }, public: { EXPO_PUBLIC_*: values } }`

At startup, the server loads `server` into memory. The `/config` endpoint returns `public` plus any `EXPO_PUBLIC_*` env vars.

Seed script: `CardTraders-infra/infra/scripts/seed_config_mongo.py`

Example:

```
MONGODB_URI='...' MONGODB_DB_NAME='cardtraders' EXPO_PUBLIC_API='https://api.example.com' \
python3 CardTraders-infra/infra/scripts/seed_config_mongo.py
```

