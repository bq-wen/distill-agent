# Production deployment (Nginx reverse proxy + HTTPS)

The project is designed to run as a **single Uvicorn worker** (the bounded queue
is an in-memory process structure). A typical production layout:

```
浏览器 ──443──> Nginx (TLS) ──8000──> distill-agent (uvicorn, 1 worker)
```

## 1. Run the app behind Nginx

Start the stack (choose one):

```bash
docker compose up -d --build
# or without Docker:
# uvicorn personal_agent.api.bootstrap:create_production_app --factory --host 127.0.0.1 --port 8000
```

## 2. Nginx site config

Copy `nginx.conf.example` to `/etc/nginx/sites-available/distill-agent`, replace
`YOUR_DOMAIN` and enable it:

```bash
sudo ln -s /etc/nginx/sites-available/distill-agent /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## 3. HTTPS with Let's Encrypt

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d YOUR_DOMAIN
# certbot edits the site config and installs automatic renewal
```

## 4. Verify

- `curl -I https://YOUR_DOMAIN/` → 200
- `curl https://YOUR_DOMAIN/health` → `{"status":"ok"}` (healthcheck endpoint)

## Notes

- Keep exactly **one** uvicorn worker. Horizontal scaling requires a shared
  scheduling layer (Redis/Celery) first.
- First start downloads the embedding model (default `BAAI/bge-small-zh-v1.5`).
  On networks without huggingface.co access set `HF_ENDPOINT=https://hf-mirror.com`
  in `.env`; for zero-download starts pre-download the model into the image.
- The API is rate-limited per client (`PERSONAL_AGENT_RATE_LIMIT_PER_MINUTE`,
  default 30/min); Nginx `limit_req` can add a coarse first line of defense.
