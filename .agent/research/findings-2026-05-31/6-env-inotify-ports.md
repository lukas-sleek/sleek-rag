# 6. Environment: inotify watch limit + dev/prod ports

`npm run dev` (Turbopack) crashed with `OS file watch limit reached`. Fixed by
raising the kernel limit (was 65536):
```
echo 'fs.inotify.max_user_watches=524288' | sudo tee /etc/sysctl.d/99-inotify.conf && sudo sysctl --system
```

Ports on this host:
- **Dev** (`npm run dev`): web `:3000`, api `:8001`.
- **Production** (same host): web `:3001` (next-server) + api `:8000` (uvicorn)
  behind nginx.

No overlap, and `concurrently --kill-others-on-fail` only kills its own
children, so `npm run dev` is safe to run alongside prod. Never `pkill` broadly.
