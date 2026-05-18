use std::collections::HashMap;
use std::str::FromStr;

use anyhow::{Context, Result};
use chrono::{SecondsFormat, Utc};
use futures::StreamExt as _;
use polymarket_client_sdk_v2::clob::ws::{
    BookUpdate, ChannelType, Client as MarketClient, PriceChange,
};
use polymarket_client_sdk_v2::types::U256;
use polymarket_client_sdk_v2::ws::config::Config as WsConfig;
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::io::{self, AsyncBufReadExt as _, AsyncWriteExt as _, BufReader, BufWriter, Stdout};
use tokio::sync::{Mutex, mpsc};
use tokio::task::JoinHandle;

const CONTROL_CONTRACT: &str = "bro.market_stream.control.v1";
const EVENT_CONTRACT: &str = "bro.market_stream.event.v1";
const PROVIDER: &str = "rs-clob-client-v2";
const CONTRACT_VERSION: &str = "v1";

#[derive(Debug, Clone, Deserialize)]
struct ControlMessage {
    contract: String,
    op: String,
    request_id: Option<String>,
    endpoint: Option<String>,
    token_ids: Option<Vec<String>>,
}

#[derive(Debug, Clone, Default)]
struct TopState {
    best_bid_price: Option<f64>,
    best_bid_size: Option<f64>,
    best_ask_price: Option<f64>,
    best_ask_size: Option<f64>,
    source: String,
    source_ts_utc: Option<String>,
}

enum InternalEvent {
    Book(BookUpdate),
    PriceChange(PriceChange),
    StreamError(String),
}

struct SubscriptionHandle {
    client: MarketClient,
    token_ids: Vec<String>,
    book_task: JoinHandle<()>,
    price_task: JoinHandle<()>,
}

impl SubscriptionHandle {
    fn subscription_state(&self) -> &'static str {
        "active"
    }

    fn connected(&self) -> bool {
        self.client.connection_state(ChannelType::Market).is_connected()
    }

    fn abort(self) {
        self.book_task.abort();
        self.price_task.abort();
    }
}

struct WorkerState {
    stdout: Mutex<BufWriter<Stdout>>,
    subscription: Option<SubscriptionHandle>,
    top_by_token: HashMap<String, TopState>,
    last_error: Option<String>,
}

impl WorkerState {
    fn new(stdout: Stdout) -> Self {
        Self {
            stdout: Mutex::new(BufWriter::new(stdout)),
            subscription: None,
            top_by_token: HashMap::new(),
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

    async fn emit_health(&self) -> Result<()> {
        let (connected, transport_connected, subscription_state, token_ids) =
            if let Some(sub) = &self.subscription {
                (
                    sub.connected(),
                    sub.connected(),
                    sub.subscription_state(),
                    sub.token_ids.clone(),
                )
            } else {
                (false, false, "idle", Vec::new())
            };
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
            "reconnects_total": 0,
            "watch_token_ids": token_ids,
            "last_error": self.last_error,
            "received_ts_utc": now_iso(),
        }))
        .await
    }

    async fn configure(&mut self, message: ControlMessage, tx: mpsc::UnboundedSender<InternalEvent>) -> Result<()> {
        if let Some(existing) = self.subscription.take() {
            existing.abort();
        }
        self.top_by_token.clear();
        self.last_error = None;

        let endpoint = message
            .endpoint
            .clone()
            .unwrap_or_else(|| "wss://ws-subscriptions-clob.polymarket.com".to_owned());
        let token_ids = unique_ordered(message.token_ids.unwrap_or_default());
        if token_ids.is_empty() {
            self.emit(json!({
                "contract": EVENT_CONTRACT,
                "event": "ack",
                "op": "configure_market_watch",
                "request_id": message.request_id,
                "provider": PROVIDER,
                "contract_version": CONTRACT_VERSION,
                "subscription_state": "idle",
                "token_count": 0,
                "received_ts_utc": now_iso(),
            }))
            .await?;
            return self.emit_health().await;
        }

        let asset_ids = parse_asset_ids(&token_ids)?;
        let client = MarketClient::new(&endpoint, WsConfig::default())
            .with_context(|| format!("market client init failed for endpoint {endpoint}"))?;
        let mut book_stream = Box::pin(client.subscribe_orderbook(asset_ids.clone())?);
        let mut price_stream = Box::pin(client.subscribe_prices(asset_ids.clone())?);

        let book_tx = tx.clone();
        let book_task = tokio::spawn(async move {
            while let Some(item) = book_stream.next().await {
                match item {
                    Ok(book) => {
                        let _ = book_tx.send(InternalEvent::Book(book));
                    }
                    Err(err) => {
                        let _ = book_tx.send(InternalEvent::StreamError(err.to_string()));
                    }
                }
            }
        });

        let price_tx = tx.clone();
        let price_task = tokio::spawn(async move {
            while let Some(item) = price_stream.next().await {
                match item {
                    Ok(price_change) => {
                        let _ = price_tx.send(InternalEvent::PriceChange(price_change));
                    }
                    Err(err) => {
                        let _ = price_tx.send(InternalEvent::StreamError(err.to_string()));
                    }
                }
            }
        });

        self.subscription = Some(SubscriptionHandle {
            client,
            token_ids: token_ids.clone(),
            book_task,
            price_task,
        });

        self.emit(json!({
            "contract": EVENT_CONTRACT,
            "event": "ack",
            "op": "configure_market_watch",
            "request_id": message.request_id,
            "provider": PROVIDER,
            "contract_version": CONTRACT_VERSION,
            "subscription_state": "configured",
            "token_count": token_ids.len(),
            "received_ts_utc": now_iso(),
        }))
        .await?;
        self.emit_health().await
    }

    async fn handle_internal_event(&mut self, event: InternalEvent) -> Result<()> {
        match event {
            InternalEvent::Book(book) => {
                let token_id = book.asset_id.to_string();
                let top = {
                    let top = self.top_by_token.entry(token_id.clone()).or_default();
                    let (bid_price, bid_size) = best_bid(&book);
                    let (ask_price, ask_size) = best_ask(&book);
                    top.best_bid_price = bid_price;
                    top.best_bid_size = bid_size;
                    top.best_ask_price = ask_price;
                    top.best_ask_size = ask_size;
                    top.source = "official_ws_book".to_owned();
                    top.source_ts_utc = Some(iso_from_millis(book.timestamp));
                    top.clone()
                };
                self.emit_top(&token_id, top).await?;
            }
            InternalEvent::PriceChange(change) => {
                let source_ts_utc = iso_from_millis(change.timestamp);
                for entry in change.price_changes {
                    let token_id = entry.asset_id.to_string();
                    let top = {
                        let top = self.top_by_token.entry(token_id.clone()).or_default();
                        if let Some(best_bid) = entry.best_bid {
                            top.best_bid_price = decimal_to_f64(&best_bid);
                        } else if side_is_buy(&entry.side) {
                            top.best_bid_price = decimal_to_f64(&entry.price);
                        }
                        if let Some(size) = &entry.size {
                            if side_is_buy(&entry.side) {
                                top.best_bid_size = decimal_to_f64(size);
                            } else {
                                top.best_ask_size = decimal_to_f64(size);
                            }
                        }
                        if let Some(best_ask) = entry.best_ask {
                            top.best_ask_price = decimal_to_f64(&best_ask);
                        } else if side_is_sell(&entry.side) {
                            top.best_ask_price = decimal_to_f64(&entry.price);
                        }
                        top.source = "official_ws_price_change".to_owned();
                        top.source_ts_utc = Some(source_ts_utc.clone());
                        top.clone()
                    };
                    self.emit_top(&token_id, top).await?;
                }
            }
            InternalEvent::StreamError(err) => {
                self.last_error = Some(err);
                self.emit_health().await?;
            }
        }
        Ok(())
    }

    async fn emit_top(&self, token_id: &str, top: TopState) -> Result<()> {
        self.emit(json!({
            "contract": EVENT_CONTRACT,
            "event": "top",
            "provider": PROVIDER,
            "contract_version": CONTRACT_VERSION,
            "token_id": token_id,
            "source": top.source,
            "best_bid_price": top.best_bid_price,
            "best_bid_size": top.best_bid_size,
            "best_ask_price": top.best_ask_price,
            "best_ask_size": top.best_ask_size,
            "source_ts_utc": top.source_ts_utc,
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
    let (tx, mut rx) = mpsc::unbounded_channel::<InternalEvent>();
    let mut health_tick = tokio::time::interval(std::time::Duration::from_secs(2));
    health_tick.tick().await;

    loop {
        tokio::select! {
            line = lines.next_line() => {
                let Some(line) = line? else { break; };
                let trimmed = line.trim();
                if trimmed.is_empty() {
                    continue;
                }
                let message: ControlMessage = match serde_json::from_str(trimmed) {
                    Ok(parsed) => parsed,
                    Err(err) => {
                        state.last_error = Some(format!("control_decode_failed:{err}"));
                        state.emit_health().await?;
                        continue;
                    }
                };
                if message.contract != CONTROL_CONTRACT {
                    continue;
                }
                match message.op.as_str() {
                    "configure_market_watch" => {
                        if let Err(err) = state.configure(message.clone(), tx.clone()).await {
                            state.last_error = Some(err.to_string());
                            state.emit(json!({
                                "contract": EVENT_CONTRACT,
                                "event": "ack",
                                "op": "configure_market_watch",
                                "request_id": message.request_id,
                                "provider": PROVIDER,
                                "contract_version": CONTRACT_VERSION,
                                "subscription_state": "error",
                                "error": err.to_string(),
                                "received_ts_utc": now_iso(),
                            })).await?;
                            state.emit_health().await?;
                        }
                    }
                    "shutdown" => break,
                    _ => {
                        state.last_error = Some(format!("unsupported_op:{}", message.op));
                        state.emit_health().await?;
                    }
                }
            }
            maybe_event = rx.recv() => {
                if let Some(event) = maybe_event {
                    state.handle_internal_event(event).await?;
                }
            }
            _ = health_tick.tick() => {
                state.emit_health().await?;
            }
        }
    }

    if let Some(existing) = state.subscription.take() {
        existing.abort();
    }
    Ok(())
}

fn unique_ordered(values: Vec<String>) -> Vec<String> {
    let mut out = Vec::new();
    let mut seen = std::collections::HashSet::new();
    for value in values {
        if value.trim().is_empty() {
            continue;
        }
        if seen.insert(value.clone()) {
            out.push(value);
        }
    }
    out
}

fn parse_asset_ids(token_ids: &[String]) -> Result<Vec<U256>> {
    token_ids
        .iter()
        .map(|token_id| U256::from_str(token_id).with_context(|| format!("invalid token id {token_id}")))
        .collect()
}

fn decimal_to_f64(value: &impl ToString) -> Option<f64> {
    value.to_string().parse::<f64>().ok()
}

fn best_bid(book: &BookUpdate) -> (Option<f64>, Option<f64>) {
    book.bids
        .iter()
        .max_by(|lhs, rhs| lhs.price.cmp(&rhs.price))
        .map(|level| (decimal_to_f64(&level.price), decimal_to_f64(&level.size)))
        .unwrap_or((None, None))
}

fn best_ask(book: &BookUpdate) -> (Option<f64>, Option<f64>) {
    book.asks
        .iter()
        .min_by(|lhs, rhs| lhs.price.cmp(&rhs.price))
        .map(|level| (decimal_to_f64(&level.price), decimal_to_f64(&level.size)))
        .unwrap_or((None, None))
}

fn side_is_buy(side: &impl std::fmt::Debug) -> bool {
    let text = format!("{side:?}").to_ascii_uppercase();
    text.contains("BUY") || text.contains("BID")
}

fn side_is_sell(side: &impl std::fmt::Debug) -> bool {
    let text = format!("{side:?}").to_ascii_uppercase();
    text.contains("SELL") || text.contains("ASK")
}

fn now_iso() -> String {
    Utc::now().to_rfc3339_opts(SecondsFormat::Millis, true)
}

fn health_usable(subscription_state: &str) -> bool {
    !matches!(subscription_state, "error" | "disconnected")
}

fn iso_from_millis(timestamp_ms: i64) -> String {
    chrono::DateTime::<Utc>::from_timestamp_millis(timestamp_ms)
        .unwrap_or_else(Utc::now)
        .to_rfc3339_opts(SecondsFormat::Millis, true)
}
