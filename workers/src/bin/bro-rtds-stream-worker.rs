use std::collections::HashSet;
use std::pin::Pin;

use anyhow::{Context, Result};
use chrono::{SecondsFormat, Utc};
use futures::{Stream, StreamExt as _, stream};
use polymarket_client_sdk_v2::rtds::Client as RtdsClient;
use polymarket_client_sdk_v2::rtds::ChainlinkPrice;
use polymarket_client_sdk_v2::ws::config::Config as WsConfig;
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::io::{self, AsyncBufReadExt as _, AsyncWriteExt as _, BufReader, BufWriter, Lines, Stdout};
use tokio::sync::Mutex;

const CONTROL_CONTRACT: &str = "bro.rtds_stream.control.v1";
const EVENT_CONTRACT: &str = "bro.rtds_stream.event.v1";
const PROVIDER: &str = "rs-clob-client-v2";
const CONTRACT_VERSION: &str = "v1";

#[derive(Debug, Clone, Deserialize)]
struct ControlMessage {
    contract: String,
    op: String,
    request_id: Option<String>,
    endpoint: Option<String>,
    topic: Option<String>,
    symbols: Option<Vec<String>>,
}

struct WorkerState {
    stdout: Mutex<BufWriter<Stdout>>,
    last_error: Option<String>,
}

struct ActiveConfig {
    request_id: Option<String>,
    endpoint: String,
    topic: String,
    symbols: Vec<String>,
}

enum LoopControl {
    Reconfigure(ControlMessage),
    Shutdown,
}

impl WorkerState {
    fn new(stdout: Stdout) -> Self {
        Self {
            stdout: Mutex::new(BufWriter::new(stdout)),
            last_error: None,
        }
    }

    async fn emit(&self, payload: Value) -> Result<()> {
        let mut stdout = self.stdout.lock().await;
        let line = serde_json::to_string(&payload)?;
        stdout.write_all(line.as_bytes()).await?;
        stdout.write_all(b"\n").await?;
        stdout.flush().await?;
        Ok(())
    }

    async fn emit_health(
        &self,
        connected: bool,
        transport_connected: bool,
        subscription_state: &str,
        topic: &str,
        symbols: &[String],
    ) -> Result<()> {
        self.emit(json!({
            "contract": EVENT_CONTRACT,
            "event": "health",
            "provider": PROVIDER,
            "contract_version": CONTRACT_VERSION,
            "connected": connected,
            "transport_connected": transport_connected,
            "usable": health_usable(subscription_state),
            "fatal_reason": serde_json::Value::Null,
            "restart_exhausted": false,
            "subscription_state": subscription_state,
            "topic": topic,
            "symbols": symbols,
            "reconnects_total": 0,
            "last_error": self.last_error,
            "received_ts_utc": now_iso(),
        }))
        .await
    }
}

#[tokio::main]
async fn main() -> Result<()> {
    let stdin = BufReader::new(io::stdin());
    let stdout = io::stdout();
    let mut lines = stdin.lines();
    let mut state = WorkerState::new(stdout);

    let mut pending_config: Option<ControlMessage> = None;
    loop {
        let message = if let Some(message) = pending_config.take() {
            message
        } else {
            let Some(message) = read_control_message(&mut lines, &mut state).await? else {
                break;
            };
            message
        };

        if message.contract != CONTROL_CONTRACT {
            continue;
        }
        match message.op.as_str() {
            "configure_rtds" => match parse_active_config(message.clone()) {
                Ok(config) => match run_active_subscription(&mut lines, &mut state, config).await? {
                    LoopControl::Reconfigure(next) => {
                        pending_config = Some(next);
                    }
                    LoopControl::Shutdown => break,
                },
                Err(err) => {
                    state.last_error = Some(err.to_string());
                    state
                        .emit(json!({
                            "contract": EVENT_CONTRACT,
                            "event": "ack",
                            "op": "configure_rtds",
                            "request_id": message.request_id,
                            "provider": PROVIDER,
                            "contract_version": CONTRACT_VERSION,
                            "subscription_state": "error",
                            "error": err.to_string(),
                            "received_ts_utc": now_iso(),
                        }))
                        .await?;
                    state
                        .emit_health(
                            false,
                            false,
                            "error",
                            "",
                            &[],
                        )
                        .await?;
                }
            },
            "shutdown" => break,
            _ => {
                state.last_error = Some(format!("unsupported_op:{}", message.op));
                state
                    .emit_health(
                        false,
                        false,
                        "idle",
                        "",
                        &[],
                    )
                    .await?;
            }
        }
    }

    Ok(())
}

async fn run_active_subscription(
    lines: &mut Lines<BufReader<tokio::io::Stdin>>,
    state: &mut WorkerState,
    config: ActiveConfig,
) -> Result<LoopControl> {
    if config.symbols.is_empty() {
        state
            .emit(json!({
                "contract": EVENT_CONTRACT,
                "event": "ack",
                "op": "configure_rtds",
                "request_id": config.request_id,
                "provider": PROVIDER,
                "contract_version": CONTRACT_VERSION,
                "subscription_state": "idle",
                "symbol_count": 0,
                "received_ts_utc": now_iso(),
            }))
            .await?;
        state
            .emit_health(
                false,
                false,
                "idle",
                &config.topic,
                &config.symbols,
            )
            .await?;
        return Ok(LoopControl::Reconfigure(
            read_control_message(lines, state).await?.unwrap_or(ControlMessage {
                contract: CONTROL_CONTRACT.to_owned(),
                op: "shutdown".to_owned(),
                request_id: None,
                endpoint: None,
                topic: None,
                symbols: None,
            }),
        ));
    }

    let client = RtdsClient::new(&config.endpoint, WsConfig::default())
        .with_context(|| format!("RTDS client init failed for endpoint {}", config.endpoint))?;
    let mut stream = build_chainlink_stream(&client, &config.symbols)?;
    let mut health_tick = tokio::time::interval(std::time::Duration::from_secs(2));
    health_tick.tick().await;

    state.last_error = None;
    state
        .emit(json!({
            "contract": EVENT_CONTRACT,
            "event": "ack",
            "op": "configure_rtds",
            "request_id": config.request_id,
            "provider": PROVIDER,
            "contract_version": CONTRACT_VERSION,
            "subscription_state": "configured",
            "symbol_count": config.symbols.len(),
            "received_ts_utc": now_iso(),
        }))
        .await?;
    state
        .emit_health(
            client.connection_state().is_connected(),
            client.connection_state().is_connected(),
            "configured",
            &config.topic,
            &config.symbols,
        )
        .await?;

    loop {
        tokio::select! {
            line = lines.next_line() => {
                let Some(line) = line? else { return Ok(LoopControl::Shutdown); };
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                let message: ControlMessage = match serde_json::from_str(trimmed) {
                    Ok(parsed) => parsed,
                    Err(err) => {
                        state.last_error = Some(format!("control_decode_failed:{err}"));
                        state.emit_health(
                            client.connection_state().is_connected(),
                            client.connection_state().is_connected(),
                            "active",
                            &config.topic,
                            &config.symbols,
                        ).await?;
                        continue;
                    }
                };
                if message.contract != CONTROL_CONTRACT {
                    continue;
                }
                match message.op.as_str() {
                    "configure_rtds" => return Ok(LoopControl::Reconfigure(message)),
                    "shutdown" => return Ok(LoopControl::Shutdown),
                    _ => {
                        state.last_error = Some(format!("unsupported_op:{}", message.op));
                        state.emit_health(
                            client.connection_state().is_connected(),
                            client.connection_state().is_connected(),
                            "active",
                            &config.topic,
                            &config.symbols,
                        ).await?;
                    }
                }
            }
            maybe_tick = stream.next() => {
                match maybe_tick {
                    Some(Ok(tick)) => {
                        let symbol = normalize_symbol(&tick.symbol);
                        state.emit(json!({
                            "contract": EVENT_CONTRACT,
                            "event": "tick",
                            "provider": PROVIDER,
                            "contract_version": CONTRACT_VERSION,
                            "symbol": symbol,
                            "price": decimal_to_f64(&tick.value),
                            "topic": "crypto_prices_chainlink",
                            "msg_type": "update",
                            "source_ts_utc": iso_from_millis(tick.timestamp),
                            "received_ts_utc": now_iso(),
                        })).await?;
                    }
                    Some(Err(err)) => {
                        state.last_error = Some(err.to_string());
                        state.emit_health(
                            client.connection_state().is_connected(),
                            client.connection_state().is_connected(),
                            "active",
                            &config.topic,
                            &config.symbols,
                        ).await?;
                    }
                    None => {
                        state.last_error = Some("rtds_stream_ended".to_owned());
                        state.emit_health(
                            false,
                            false,
                            "disconnected",
                            &config.topic,
                            &config.symbols,
                        ).await?;
                        return Ok(LoopControl::Shutdown);
                    }
                }
            }
            _ = health_tick.tick() => {
                state.emit_health(
                    client.connection_state().is_connected(),
                    client.connection_state().is_connected(),
                    "active",
                    &config.topic,
                    &config.symbols,
                ).await?;
            }
        }
    }
}

async fn read_control_message(
    lines: &mut Lines<BufReader<tokio::io::Stdin>>,
    state: &mut WorkerState,
) -> Result<Option<ControlMessage>> {
    loop {
        let Some(line) = lines.next_line().await? else { return Ok(None); };
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let message: ControlMessage = match serde_json::from_str(trimmed) {
            Ok(parsed) => parsed,
            Err(err) => {
                state.last_error = Some(format!("control_decode_failed:{err}"));
                state
                    .emit_health(
                        false,
                        false,
                        "idle",
                        "",
                        &[],
                    )
                    .await?;
                continue;
            }
        };
        return Ok(Some(message));
    }
}

fn parse_active_config(message: ControlMessage) -> Result<ActiveConfig> {
    let endpoint = message
        .endpoint
        .clone()
        .unwrap_or_else(|| "wss://ws-live-data.polymarket.com".to_owned());
    let topic = message
        .topic
        .clone()
        .unwrap_or_else(|| "crypto_prices_chainlink".to_owned());
    if topic != "crypto_prices_chainlink" {
        anyhow::bail!("unsupported RTDS topic: {topic}");
    }
    Ok(ActiveConfig {
        request_id: message.request_id,
        endpoint,
        topic,
        symbols: normalize_symbols(message.symbols.unwrap_or_default()),
    })
}

fn health_usable(subscription_state: &str) -> bool {
    !matches!(subscription_state, "error" | "disconnected")
}

fn normalize_symbols(values: Vec<String>) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = HashSet::new();
    for value in values {
        let normalized = normalize_symbol(&value);
        if normalized.is_empty() {
            continue;
        }
        if seen.insert(normalized.clone()) {
            out.push(normalized);
        }
    }
    out
}

fn build_chainlink_stream<'a>(
    client: &'a RtdsClient,
    symbols: &'a [String],
) -> Result<Pin<Box<dyn Stream<Item = Result<ChainlinkPrice>> + Send + 'a>>> {
    if symbols.is_empty() {
        return Ok(Box::pin(
            client
                .subscribe_chainlink_prices(None)?
                .map(|item| item.map_err(anyhow::Error::from)),
        ));
    }
    if symbols.len() == 1 {
        return Ok(Box::pin(
            client
                .subscribe_chainlink_prices(Some(symbols[0].clone()))?
                .map(|item| item.map_err(anyhow::Error::from)),
        ));
    }
    let mut streams: Vec<Pin<Box<dyn Stream<Item = Result<ChainlinkPrice>> + Send>>> = Vec::new();
    for symbol in symbols {
        let stream = client
            .subscribe_chainlink_prices(Some(symbol.clone()))?
            .map(|item| item.map_err(anyhow::Error::from));
        streams.push(Box::pin(stream));
    }
    Ok(Box::pin(stream::select_all(streams)))
}

fn normalize_symbol(value: &str) -> String {
    let text = value.trim().to_ascii_lowercase();
    if text.is_empty() {
        return String::new();
    }
    if text.contains('/') {
        return text;
    }
    let compact: String = text.chars().filter(|ch| ch.is_ascii_alphanumeric()).collect();
    if compact.ends_with("usdt") && compact.len() > 4 {
        return format!("{}/usd", &compact[..compact.len() - 4]);
    }
    if compact.ends_with("usdc") && compact.len() > 4 {
        return format!("{}/usd", &compact[..compact.len() - 4]);
    }
    if compact.ends_with("usd") && compact.len() > 3 {
        return format!("{}/usd", &compact[..compact.len() - 3]);
    }
    compact
}

fn decimal_to_f64(value: &impl ToString) -> Option<f64> {
    value.to_string().parse::<f64>().ok()
}

fn now_iso() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true)
}

fn iso_from_millis(timestamp_ms: i64) -> String {
    chrono::DateTime::<Utc>::from_timestamp_millis(timestamp_ms)
        .unwrap_or_else(Utc::now)
        .to_rfc3339_opts(SecondsFormat::Millis, true)
}
