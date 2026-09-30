# Deploying WE CARE Billing with PM2

Tested on Ubuntu 22.04 / 24.04 VPS (works the same on any Linux box).

> **Single process only.** The app stores data in Apache Feather files, not in a
> database server. Run exactly **one** instance (`exec_mode: fork`, `instances: 1`,
> `uvicorn --workers 1`). Two writers would corrupt `data/*.feather`.

---

## 1. Install system packages

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip git nginx curl

# Node.js (only needed so we can run PM2)
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g pm2
```

## 2. Get the code

```bash
sudo mkdir -p /var/www && sudo chown $USER:$USER /var/www
cd /var/www
git clone https://github.com/Mr-argha-das/wecare_inventroy.git wecare
cd wecare
```

## 3. Python virtualenv + dependencies

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

## 4. Configure `.env`

```bash
cp .env.example .env
# generate a strong secret:
.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(48))"
nano .env
```

Set at minimum:

```ini
SECRET_KEY=<the long random value you just generated>
DEFAULT_ADMIN_USERNAME=admin
DEFAULT_ADMIN_PASSWORD=<a strong password — change it, then change it again in the UI>
TIMEZONE=Asia/Kolkata
```

## 5. Start with PM2

The repo ships with `ecosystem.config.js`, so:

```bash
pm2 start ecosystem.config.js --env production
pm2 logs wecare-billing          # watch startup output
pm2 list
```

Check it locally:

```bash
curl -I http://127.0.0.1:8000/auth/login     # expect 200
```

Make PM2 survive reboots:

```bash
pm2 save
pm2 startup          # prints one `sudo env PATH=... pm2 startup systemd -u <user> ...`
                     # command — copy-paste and run it
```

### Everyday PM2 commands

| Command | What it does |
|---|---|
| `pm2 list` | status of the app |
| `pm2 logs wecare-billing` | live logs (also in `logs/pm2-*.log`) |
| `pm2 restart wecare-billing` | restart after a code change |
| `pm2 reload wecare-billing` | graceful restart |
| `pm2 stop wecare-billing` | stop |
| `pm2 monit` | live CPU / RAM dashboard |
| `pm2 flush` | clear log files |

## 6. Nginx reverse proxy (port 80 → 8000)

`/etc/nginx/sites-available/wecare`:

```nginx
server {
    listen 80;
    server_name billing.example.com;      # your domain or server IP

    client_max_body_size 10M;             # logo / signature / QR uploads

    # Serve static assets directly (faster than going through Python)
    location /static/ {
        alias /var/www/wecare/app/static/;
        expires 7d;
        access_log off;
    }

    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
        proxy_read_timeout 120s;          # PDF generation headroom
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/wecare /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

## 7. HTTPS (free, recommended)

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d billing.example.com
```

Certbot renews automatically. Sessions are cookie based, so HTTPS matters.

## 8. Firewall

```bash
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
# Port 8000 stays closed to the internet — only nginx talks to it.
```

---

## Updating to a new version

```bash
cd /var/www/wecare
pm2 stop wecare-billing            # stop writers before touching data
cp -r data data-backup-$(date +%F) # cheap safety net
git pull
.venv/bin/pip install -r requirements.txt
pm2 restart wecare-billing
pm2 logs wecare-billing --lines 50
```

## Backups

* In-app: **Settings → Backup → Create Backup Now** (writes `backups/backup_YYYY_MM_DD_HHMMSS/`).
* Off-server nightly copy (cron):

```bash
crontab -e
# every night at 1:30 AM
30 1 * * * tar -czf /var/backups/wecare-$(date +\%F).tar.gz -C /var/www/wecare data documents app/static/uploads
```

Keep `data/`, `documents/` and `app/static/uploads/` — that is your entire business
data (records, generated PDFs, logo / signature / QR images).

## Troubleshooting

| Symptom | Fix |
|---|---|
| `Script not found: .../.venv/bin/python` | virtualenv missing — re-run step 3 |
| PM2 shows `errored` / restart loop | `pm2 logs wecare-billing --err --lines 100` |
| 502 Bad Gateway from nginx | app is down (`pm2 list`) or wrong port in `proxy_pass` |
| Logo / QR upload fails | raise `client_max_body_size` in nginx and `MAX_UPLOAD_MB` in `.env` |
| Data looks stale or corrupted | you are running more than one instance — keep `instances: 1` |
| Login works but sessions drop | `SECRET_KEY` changed or differs between restarts — set it in `.env` |
