# Running LiveKit Locally with Docker Desktop

This guide sets up a fully self-hosted LiveKit stack for local development — LiveKit server, Redis (required for Egress coordination), and LiveKit Egress (recording). All three run via Docker Compose.

> **Note (Windows):** Disable **Smart App Control** in Windows Security → App & browser control → Smart App Control → Off. The Pipecat `livekit_ffi.dll` is unsigned and SAC will silently block it.

---

## Step 1 — Config files

The project root already contains `livekit.yaml`, `egress.yaml`, and `docker-compose.yml`. You do not need to create them.

**`livekit.yaml`** — LiveKit server config:
```yaml
port: 7880
rtc:
  tcp_port: 7881
  udp_port: 7882
  use_external_ip: false
  node_ip: 127.0.0.1   # advertise localhost for ICE — required for browser WebRTC

redis:
  address: redis:6379   # uses the redis service inside Docker Compose

keys:
  devkey: devsecret-local-dev-only-32charslong!!
```

**`egress.yaml`** — Egress service config:
```yaml
api_key: devkey
api_secret: devsecret-local-dev-only-32charslong!!
ws_url: ws://livekit:7880   # internal Docker network — Egress talks to LiveKit by service name

redis:
  address: redis:6379
```

**`docker-compose.yml`** — three services:
```yaml
services:
  redis:
    image: redis:alpine
    restart: unless-stopped

  livekit:
    image: livekit/livekit-server:latest
    ports:
      - "7880:7880"
      - "7881:7881"
      - "7882:7882/udp"
    volumes:
      - ./livekit.yaml:/etc/livekit.yaml
    command: --config /etc/livekit.yaml
    depends_on:
      - redis
    restart: unless-stopped

  egress:
    image: livekit/egress:latest
    environment:
      - EGRESS_CONFIG_FILE=/etc/egress.yaml
    volumes:
      - ./egress.yaml:/etc/egress.yaml
    cap_add:
      - SYS_ADMIN   # required for headless Chrome inside Egress
    depends_on:
      - livekit
      - redis
    restart: unless-stopped
```

---

## Step 2 — `.env` values for self-hosted

```env
LIVEKIT_URL=ws://localhost:7880
LIVEKIT_API_KEY=devkey
LIVEKIT_API_SECRET=devsecret-local-dev-only-32charslong!!
```

> `ws://` (not `wss://`) is fine for localhost — browsers allow insecure WebSocket to localhost.
> The secret must be at least 32 characters. `devsecret` alone is too short and will warn.

---

## Step 3 — Start everything

Open a terminal in the project root:

```bash
docker compose up -d
```

Check all three are running:

```bash
docker compose ps
```

You should see `livekit`, `redis`, and `egress` all with status `Up`.

---

## Step 4 — Start the backend

```bash
python -m uvicorn api.session:app --host 127.0.0.1 --port 8000
```

---

## Useful Docker Compose commands

```bash
docker compose ps          # list all containers + status
docker compose logs -f     # tail live logs from all services
docker compose stop        # stop all containers (keeps them)
docker compose down        # remove containers entirely
docker compose up -d       # start (or restart after config change)
```

---

## How recording works

When a session starts, `api/session.py` calls `lk.egress.start_room_composite_egress(...)` with your AWS S3 credentials embedded in the request. LiveKit Egress records the room audio and uploads the `.ogg` file directly to AWS S3. The API stores the S3 key in MongoDB. The frontend fetches a presigned URL from `GET /interview/{session_id}/recording`.

---

## Switching back to LiveKit Cloud

Revert `.env` to your Cloud credentials:

```env
LIVEKIT_URL=wss://your-project.livekit.cloud
LIVEKIT_API_KEY=your_cloud_api_key
LIVEKIT_API_SECRET=your_cloud_api_secret
```

LiveKit Cloud includes Egress built-in — no Docker setup needed.
