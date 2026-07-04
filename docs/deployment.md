# Deployment: Hetzner VPS + Cloudflare Tunnel + Access

Target architecture: a small Hetzner Cloud VPS runs the app in Docker Compose with
zero open inbound ports; `cloudflared` dials out to Cloudflare, which fronts the
app at a hostname you own and gates every request behind Cloudflare Access SSO.
The app has **no auth of its own** — never expose port 8000 publicly.

```
phone/laptop ──HTTPS──> Cloudflare edge ──(Access: SSO, email allowlist)
                              │
                        Cloudflare Tunnel (outbound-only from VPS)
                              │
                     docker network: cloudflared ──> app:8000
                                                       │
                                                stocks-data volume (/data)
```

Cost: Hetzner CX22 ≈ €3.85/mo. Cloudflare Tunnel + Access are free for this use
(Zero Trust free tier covers up to 50 users).

## 1. Create the VPS

Hetzner Cloud console → new server:

- Image: **Ubuntu 24.04**, type: **CX22** (2 vCPU / 4 GB — pandas/skfolio want the RAM)
- Add your SSH key; skip the firewall for now (we lock down below)

Then on the server:

```bash
ssh root@<server-ip>
apt-get update && apt-get -y upgrade
curl -fsSL https://get.docker.com | sh

# Only SSH is reachable; the app is never published on the host anyway.
ufw default deny incoming && ufw default allow outgoing
ufw allow OpenSSH && ufw enable

apt-get install -y unattended-upgrades
dpkg-reconfigure -plow unattended-upgrades
```

## 2. Create the tunnel + Access policy (Cloudflare dashboard)

Zero Trust dashboard (`one.dash.cloudflare.com`) — your domain must be on Cloudflare.

1. **Networks → Tunnels → Create a tunnel** (Cloudflared connector). Name it
   `stocks`. Copy the token from the `docker run` snippet it shows — that token is
   `CLOUDFLARE_TUNNEL_TOKEN` below.
2. In the tunnel's **Public Hostname** tab: hostname `stocks.<your-domain>`,
   service `http://app:8000` (`app` is the compose service name — cloudflared
   resolves it over the compose network).
3. **Access → Applications → Add an application** (Self-hosted): application
   domain `stocks.<your-domain>`; policy: Allow → Include → Emails →
   your email. Session duration to taste (e.g. 1 week). Pick a login method
   (Google one-click is the least friction).

After this, the hostname 404s until the container is up, and every request
must pass SSO first.

## 3. Deploy the app

```bash
ssh root@<server-ip>
git clone <your-repo-url> stocks && cd stocks
cp .env.example .env
# Fill in: OPENROUTER_API_KEY (required), FINNHUB_API_KEY / FRED_API_KEY /
# SEC_IDENTITY (optional), and CLOUDFLARE_TUNNEL_TOKEN from step 2.
nano .env

docker compose -f deploy/docker-compose.yml up -d --build
docker compose -f deploy/docker-compose.yml logs -f   # watch first boot
```

Visit `https://stocks.<your-domain>` — you should hit the Access login, then the
app. Confirm `docker ps` shows the app healthcheck as `healthy`.

## 4. Updating

```bash
cd stocks && git pull
docker compose -f deploy/docker-compose.yml up -d --build
```

State lives in the `stocks-data` volume (`/data`: SQLite DB + caches), so
rebuilds and container replacement never touch holdings/watchlist/reports.

## 5. Backups

Everything worth keeping is one SQLite file plus rebuildable caches. A nightly
dump of the DB is enough:

```bash
cat >/etc/cron.daily/stocks-backup <<'EOF'
#!/bin/sh
docker compose -f /root/stocks/deploy/docker-compose.yml exec -T app \
  python -c "import sqlite3; sqlite3.connect('/data/stocks.db').backup(sqlite3.connect('/data/stocks-backup.db'))"
docker run --rm -v stocks-data:/data -v /root/backups:/out alpine \
  cp /data/stocks-backup.db /out/stocks-$(date +%F).db
find /root/backups -name 'stocks-*.db' -mtime +30 -delete
EOF
chmod +x /etc/cron.daily/stocks-backup && mkdir -p /root/backups
```

(Consider syncing `/root/backups` off-box — a `rclone` cron to any object
storage — once the DB holds data you'd miss.)

## Troubleshooting

- **522/timeout at the hostname**: `docker compose logs cloudflared` — a bad
  token or the tunnel deleted in the dashboard. The app container being down
  gives a Cloudflare error page, not a browser timeout.
- **Access loop / wrong account**: the Access policy email must exactly match
  the login identity.
- **App unhealthy**: `docker compose logs app`; see `docs/operations.md` for
  the app's own failure modes and log conventions.
- The VPS having no published ports is intentional — do not add
  `ports: ["8000:8000"]` to the compose file.
