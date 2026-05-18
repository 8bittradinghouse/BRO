from __future__ import annotations

MARKET_STREAM_CONTROL_CONTRACT = "bro.market_stream.control.v1"
MARKET_STREAM_EVENT_CONTRACT = "bro.market_stream.event.v1"
RTDS_STREAM_CONTROL_CONTRACT = "bro.rtds_stream.control.v1"
RTDS_STREAM_EVENT_CONTRACT = "bro.rtds_stream.event.v1"

EVENT_ACK = "ack"
EVENT_TOP = "top"
EVENT_TICK = "tick"
EVENT_HEALTH = "health"
EVENT_FATAL = "fatal"

OP_CONFIGURE_MARKET_WATCH = "configure_market_watch"
OP_CONFIGURE_RTDS = "configure_rtds"
OP_SHUTDOWN = "shutdown"

MARKET_STREAM_PROVIDER = "rs-clob-client-v2"
RTDS_STREAM_PROVIDER = "rs-clob-client-v2"

CONTRACT_VERSION = "v1"
