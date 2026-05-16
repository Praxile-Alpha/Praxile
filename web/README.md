# Praxile Web Console

This is the optional React/Vite frontend for Praxile's local gateway.

The Python package still ships a dependency-free stdlib console. This app is a richer standalone UI that talks to the same `/api/*` gateway endpoints.

## Develop

```bash
praxile gateway serve --host 127.0.0.1 --port 8765
cd web
npm install
npm run dev
```

Set `VITE_PRAXILE_API_BASE=http://127.0.0.1:8765` if the frontend is served from a different origin.

Remote GitHub actions still require explicit confirmation and a token environment variable such as `GITHUB_TOKEN` on the gateway process.
