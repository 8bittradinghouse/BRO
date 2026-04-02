# Bro Vultr Deployment (Tokyo VPS)

This guide is for running 3 independent Bro bots (BTC/SOL/XRP) on a Vultr VPS with Docker Compose, while keeping container root FS read-only and all writes in mounted volumes.

## 1) Folder-per-asset layout

Create one directory per bot clone:

```bash
mkdir -p ~/bro-btc ~/bro-sol ~/bro-xrp
cp -r polymarket-bro/* ~/bro-btc/
cp -r polymarket-bro/* ~/bro-sol/
cp -r polymarket-bro/* ~/bro-xrp/
```

Each folder runs as its own Compose project.

## 2) Per-asset `.env` files

`~/bro-btc/.env`:

```dotenv
COMPOSE_PROJECT_NAME=bro-btc
BRO_ASSET=btc
BRO_MODE=paper
BRO_DOCKER_MODE=1
BRO_CONFIG_PATH=./configs/btc_paper_docker.yaml
BRO_LOG_DIR=./logs_btc
BRO_DATA_DIR=./data_btc
BRO_METRICS_PORT=9111
POLYMARKET_PRIVATE_KEY=replace_me
POLYMARKET_FUNDER=0xreplace_me
SECURITY_ACK=YES
```

`~/bro-sol/.env`:

```dotenv
COMPOSE_PROJECT_NAME=bro-sol
BRO_ASSET=sol
BRO_MODE=paper
BRO_DOCKER_MODE=1
BRO_CONFIG_PATH=./configs/sol_paper_docker.yaml
BRO_LOG_DIR=./logs_sol
BRO_DATA_DIR=./data_sol
BRO_METRICS_PORT=9112
POLYMARKET_PRIVATE_KEY=replace_me
POLYMARKET_FUNDER=0xreplace_me
SECURITY_ACK=YES
```

`~/bro-xrp/.env`:

```dotenv
COMPOSE_PROJECT_NAME=bro-xrp
BRO_ASSET=xrp
BRO_MODE=paper
BRO_DOCKER_MODE=1
BRO_CONFIG_PATH=./configs/xrp_paper_docker.yaml
BRO_LOG_DIR=./logs_xrp
BRO_DATA_DIR=./data_xrp
BRO_METRICS_PORT=9113
POLYMARKET_PRIVATE_KEY=replace_me
POLYMARKET_FUNDER=0xreplace_me
SECURITY_ACK=YES
```

Switch to live by changing `BRO_MODE=live` and using `*_live_docker.yaml`.

## 3) Build / up / down

Per folder:

```bash
docker compose config
docker compose up -d --build
docker compose ps
docker compose logs -f bro-maker
```

Stop:

```bash
docker compose down
```

## 4) Kill-switch behavior in Docker

- Guard file path is `runtime.guard_stop_file`.
- In docker configs it is under `/logs/<asset>_<mode>/guard_stop.txt`.
- Host path is `${BRO_LOG_DIR}/<asset>_<mode>/guard_stop.txt`.

Trigger safe stop:

```bash
mkdir -p ./logs_btc/btc_paper
echo "manual stop" > ./logs_btc/btc_paper/guard_stop.txt
```

Bro detects the file, cancels open orders, and transitions to safe-stop logic.

## 5) Read-only FS safety checks

Before launch, run:

```bash
python scripts/readiness_gate.py --config ./configs/btc_paper_docker.yaml --check-writable-paths-only
```

If path validation fails, update config paths to `/logs/...` and `/data/...` only.
