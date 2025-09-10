# Public Config Endpoint

GET `/config` returns

```
{ "config": { ... } }
```

Values come from:
1) Mongo `config.runtime.public`
2) Environment variables with prefix `EXPO_PUBLIC_` (override)

Server-only values from `config.runtime.server` are loaded into memory at startup for backend use only.
