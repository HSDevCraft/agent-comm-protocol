"""
Async Messaging Layer
Provides async communication primitives for the multi-agent system:
  - MessageBus:      publish/subscribe broker for system-wide events
  - MessageEnvelope: typed, prioritized message wrapper
  - Channel:         point-to-point async queue between two agents
  - EventEmitter:    fire-and-forget event broadcasting
  - StreamChannel:   async generator-based streaming channel (SSE simulation)
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, AsyncIterator, Callable, Coroutine


from src._logging import get_logger

logger = get_logger(__name__)


class MessageType(str, Enum):
    TASK_REQUEST = "TASK_REQUEST"
    TASK_RESPONSE = "TASK_RESPONSE"
    EVENT = "EVENT"
    HEARTBEAT = "HEARTBEAT"
    STREAM_CHUNK = "STREAM_CHUNK"
    STREAM_END = "STREAM_END"
    ERROR = "ERROR"
    BROADCAST = "BROADCAST"
    CANCEL = "CANCEL"


class MessagePriority(int, Enum):
    CRITICAL = 10
    HIGH = 7
    MEDIUM = 5
    LOW = 3
    BACKGROUND = 1


@dataclass(order=True)
class MessageEnvelope:
    """
    Typed, prioritized message for async inter-agent communication.
    `order=True` enables priority queue ordering (higher priority = processed first).
    """
    priority: int
    message_id: str = field(compare=False)
    type: MessageType = field(compare=False)
    sender_id: str = field(compare=False)
    receiver_id: str = field(compare=False)
    payload: dict[str, Any] = field(compare=False)
    correlation_id: str = field(compare=False, default="")
    timestamp: float = field(compare=False, default_factory=time.time)
    ttl_seconds: int = field(compare=False, default=300)
    reply_to: str = field(compare=False, default="")
    headers: dict[str, str] = field(compare=False, default_factory=dict)

    @classmethod
    def create(
        cls,
        sender_id: str,
        receiver_id: str,
        msg_type: MessageType,
        payload: dict[str, Any],
        priority: MessagePriority = MessagePriority.MEDIUM,
        correlation_id: str = "",
        reply_to: str = "",
        ttl_seconds: int = 300,
        headers: dict[str, str] | None = None,
    ) -> "MessageEnvelope":
        return cls(
            priority=priority.value,
            message_id=f"msg-{uuid.uuid4().hex[:8]}",
            type=msg_type,
            sender_id=sender_id,
            receiver_id=receiver_id,
            payload=payload,
            correlation_id=correlation_id or f"corr-{uuid.uuid4().hex[:8]}",
            reply_to=reply_to or sender_id,
            ttl_seconds=ttl_seconds,
            headers=headers or {},
        )

    def is_expired(self) -> bool:
        return time.time() > (self.timestamp + self.ttl_seconds)

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_id": self.message_id,
            "type": self.type.value,
            "priority": self.priority,
            "sender_id": self.sender_id,
            "receiver_id": self.receiver_id,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
            "ttl_seconds": self.ttl_seconds,
            "reply_to": self.reply_to,
            "headers": self.headers,
            "payload": self.payload,
        }


SubscriberFn = Callable[[MessageEnvelope], Coroutine[Any, Any, None]]


class Channel:
    """
    Point-to-point async priority queue between exactly two agents.
    Messages are consumed in priority order (highest first).
    """

    def __init__(self, sender_id: str, receiver_id: str) -> None:
        self.sender_id = sender_id
        self.receiver_id = receiver_id
        self.channel_id = f"{sender_id}->{receiver_id}"
        self._queue: asyncio.PriorityQueue[MessageEnvelope] = asyncio.PriorityQueue()
        self._dead_letter: list[MessageEnvelope] = []
        self._message_count = 0

    async def send(self, envelope: MessageEnvelope) -> None:
        if envelope.is_expired():
            logger.warning("channel_message_expired", channel=self.channel_id)
            self._dead_letter.append(envelope)
            return
        await self._queue.put(envelope)
        self._message_count += 1
        logger.debug("channel_send", channel=self.channel_id, msg_id=envelope.message_id)

    async def receive(self, timeout: float = 10.0) -> MessageEnvelope | None:
        try:
            envelope = await asyncio.wait_for(self._queue.get(), timeout=timeout)
            if envelope.is_expired():
                self._dead_letter.append(envelope)
                return None
            return envelope
        except asyncio.TimeoutError:
            return None

    def size(self) -> int:
        return self._queue.qsize()

    def dead_letter_count(self) -> int:
        return len(self._dead_letter)

    def stats(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "queued": self.size(),
            "dead_letter": self.dead_letter_count(),
            "total_sent": self._message_count,
        }


class StreamChannel:
    """
    Async generator channel for streaming responses (SSE simulation).
    Producers push chunks; consumers iterate with `async for`.
    """

    def __init__(self, stream_id: str) -> None:
        self.stream_id = stream_id
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._closed = False

    async def push(self, chunk: dict[str, Any]) -> None:
        if not self._closed:
            await self._queue.put(chunk)

    async def close(self) -> None:
        self._closed = True
        await self._queue.put(None)

    async def __aiter__(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            chunk = await self._queue.get()
            if chunk is None:
                break
            yield chunk


class MessageBus:
    """
    System-wide publish/subscribe message broker.

    Agents subscribe to topics (e.g., "task.completed", "agent.registered").
    Publishers fire events without knowing who the subscribers are.
    This decouples event producers from consumers.

    In production: back this with Redis Pub/Sub or Kafka.
    """

    def __init__(self) -> None:
        self._subscribers: dict[str, list[SubscriberFn]] = {}
        self._channels: dict[str, Channel] = {}
        self._stream_channels: dict[str, StreamChannel] = {}
        self._published_count = 0
        self._topic_counts: dict[str, int] = {}

    def subscribe(self, topic: str, handler: SubscriberFn) -> None:
        self._subscribers.setdefault(topic, []).append(handler)
        logger.debug("bus_subscribe", topic=topic, handler=handler.__qualname__)

    def unsubscribe(self, topic: str, handler: SubscriberFn) -> None:
        if topic in self._subscribers:
            self._subscribers[topic] = [h for h in self._subscribers[topic] if h != handler]

    async def publish(
        self,
        topic: str,
        payload: dict[str, Any],
        sender_id: str = "system",
        priority: MessagePriority = MessagePriority.MEDIUM,
    ) -> int:
        """
        Publish an event to a topic. Returns the number of subscribers notified.
        All subscriber handlers are called concurrently.
        """
        handlers = self._subscribers.get(topic, [])
        self._published_count += 1
        self._topic_counts[topic] = self._topic_counts.get(topic, 0) + 1

        if not handlers:
            logger.debug("bus_publish_no_subscribers", topic=topic)
            return 0

        envelope = MessageEnvelope.create(
            sender_id=sender_id,
            receiver_id="broadcast",
            msg_type=MessageType.BROADCAST,
            payload={"topic": topic, "data": payload},
            priority=priority,
        )

        tasks = [asyncio.create_task(h(envelope)) for h in handlers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        failed = sum(1 for r in results if isinstance(r, Exception))
        if failed:
            logger.warning("bus_publish_partial_failure", topic=topic, failed=failed, total=len(handlers))

        logger.debug("bus_publish", topic=topic, subscribers=len(handlers), failed=failed)
        return len(handlers)

    def get_channel(self, sender_id: str, receiver_id: str) -> Channel:
        """Get or create a point-to-point channel between two agents."""
        channel_id = f"{sender_id}->{receiver_id}"
        if channel_id not in self._channels:
            self._channels[channel_id] = Channel(sender_id, receiver_id)
        return self._channels[channel_id]

    def create_stream(self, stream_id: str | None = None) -> StreamChannel:
        sid = stream_id or f"stream-{uuid.uuid4().hex[:8]}"
        channel = StreamChannel(sid)
        self._stream_channels[sid] = channel
        return channel

    def get_stream(self, stream_id: str) -> StreamChannel | None:
        return self._stream_channels.get(stream_id)

    async def send_direct(
        self,
        sender_id: str,
        receiver_id: str,
        msg_type: MessageType,
        payload: dict[str, Any],
        priority: MessagePriority = MessagePriority.MEDIUM,
        correlation_id: str = "",
        ttl_seconds: int = 300,
    ) -> MessageEnvelope:
        """Send a message directly to an agent's channel."""
        channel = self.get_channel(sender_id, receiver_id)
        envelope = MessageEnvelope.create(
            sender_id=sender_id,
            receiver_id=receiver_id,
            msg_type=msg_type,
            payload=payload,
            priority=priority,
            correlation_id=correlation_id,
            ttl_seconds=ttl_seconds,
        )
        await channel.send(envelope)
        return envelope

    async def request_reply(
        self,
        sender_id: str,
        receiver_id: str,
        payload: dict[str, Any],
        handler: SubscriberFn,
        timeout: float = 30.0,
    ) -> dict[str, Any] | None:
        """
        Synchronous request-reply over async channels.
        Sends a message and waits for the handler to produce a reply.
        """
        corr_id = f"rr-{uuid.uuid4().hex[:8]}"
        envelope = await self.send_direct(
            sender_id=sender_id,
            receiver_id=receiver_id,
            msg_type=MessageType.TASK_REQUEST,
            payload=payload,
            correlation_id=corr_id,
        )

        try:
            result = await asyncio.wait_for(handler(envelope), timeout=timeout)
            return result if isinstance(result, dict) else {"result": result}
        except asyncio.TimeoutError:
            logger.warning("request_reply_timeout", sender=sender_id, receiver=receiver_id, timeout=timeout)
            return None

    def bus_stats(self) -> dict[str, Any]:
        return {
            "total_published": self._published_count,
            "active_channels": len(self._channels),
            "active_streams": len(self._stream_channels),
            "subscribers_by_topic": {t: len(h) for t, h in self._subscribers.items()},
            "published_by_topic": self._topic_counts,
        }


class EventEmitter:
    """
    Lightweight event emitter for agent-local events.
    Decouples agent internals — components emit events without knowing listeners.
    """

    def __init__(self, owner_id: str) -> None:
        self.owner_id = owner_id
        self._listeners: dict[str, list[Callable[..., Any]]] = {}

    def on(self, event: str, listener: Callable[..., Any]) -> None:
        self._listeners.setdefault(event, []).append(listener)

    def off(self, event: str, listener: Callable[..., Any]) -> None:
        if event in self._listeners:
            self._listeners[event] = [l for l in self._listeners[event] if l != listener]

    async def emit(self, event: str, **kwargs: Any) -> None:
        for listener in self._listeners.get(event, []):
            try:
                if asyncio.iscoroutinefunction(listener):
                    await listener(**kwargs)
                else:
                    listener(**kwargs)
            except Exception as exc:
                logger.error("event_listener_error", event=event, error=str(exc))
