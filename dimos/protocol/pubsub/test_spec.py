#!/usr/bin/env python3

# Copyright 2025 Dimensional Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import multiprocessing
import time
import traceback
from contextlib import contextmanager
from typing import Any, Callable, List, Tuple

import pytest

from dimos.msgs.geometry_msgs import Vector3
from dimos.protocol.pubsub.lcmpubsub import LCM, Topic
from dimos.protocol.pubsub.memory import Memory
from dimos.protocol.pubsub.shmpubsub import SharedMemory


@contextmanager
def memory_context():
    """Context manager for Memory PubSub implementation."""
    memory = Memory()
    try:
        yield memory
    finally:
        # Cleanup logic can be added here if needed
        pass


# Use Any for context manager type to accommodate both Memory and Redis
testdata: List[Tuple[Callable[[], Any], Any, List[Any]]] = [
    #    (memory_context, "topic", ["value1", "value2", "value3"]),
]

try:
    from dimos.protocol.pubsub.redispubsub import Redis

    @contextmanager
    def redis_context():
        redis_pubsub = Redis()
        redis_pubsub.start()
        yield redis_pubsub
        redis_pubsub.stop()

    testdata.append(
        (redis_context, "redis_topic", ["redis_value1", "redis_value2", "redis_value3"])
    )

except (ConnectionError, ImportError):
    # either redis is not installed or the server is not running
    print("Redis not available")


@contextmanager
def lcm_context():
    lcm_pubsub = LCM(autoconf=True)
    lcm_pubsub.start()
    yield lcm_pubsub
    lcm_pubsub.stop()


testdata.append(
    (
        lcm_context,
        Topic(topic="/test_topic", lcm_type=Vector3),
        [Vector3(1, 2, 3), Vector3(4, 5, 6), Vector3(7, 8, 9)],  # Using Vector3 as mock data,
    )
)


@contextmanager
def shared_memory_cpu_context():
    shared_mem_pubsub = SharedMemory(prefer="cpu")
    shared_mem_pubsub.start()
    yield shared_mem_pubsub
    shared_mem_pubsub.stop()


@contextmanager
def shared_memory_cuda_context():
    shared_mem_pubsub = SharedMemory(prefer="cuda")
    shared_mem_pubsub.start()
    yield shared_mem_pubsub
    shared_mem_pubsub.stop()


testdata.append(
    (
        shared_memory_cpu_context,
        "/shared_mem_topic_cpu",
        [b"shared_mem_value1", b"shared_mem_value2", b"shared_mem_value3"],
    )
)

testdata.append(
    (
        shared_memory_cuda_context,
        "/shared_mem_topic_cuda",
        [b"shared_mem_value1", b"shared_mem_value2", b"shared_mem_value3"],
    )
)


@pytest.mark.parametrize("pubsub_context, topic, values", testdata)
def test_pubsub_multiprocess(pubsub_context, topic, values):
    def publisher_fork():
        with pubsub_context() as pubsub:
            for value in values:
                pubsub.publish(topic, value)

    received = []

    def receive_msg(msg, topic):
        received.append(msg)

    with pubsub_context() as pubsub:
        pubsub.subscribe(topic, receive_msg)

        pub_process = multiprocessing.Process(target=publisher_fork)
        pub_process.start()

        timeout = 1.0
        start = time.time()
        while len(received) < len(values) and (time.time() - start) < timeout:
            time.sleep(0.01)

        pub_process.join(timeout=timeout)

    print(f"Received messages: {received}")
    assert len(received) == len(values), f"Expected {len(values)} messages, got {len(received)}"
    assert received == values, f"Messages don't match: {received} != {values}"


@pytest.mark.parametrize("pubsub_context, topic, values", testdata)
def test_multiple_subscribers(pubsub_context, topic, values):
    """Test that multiple subscribers receive the same message."""
    with pubsub_context() as x:
        # Create lists to capture received messages for each subscriber
        received_messages_1 = []
        received_messages_2 = []

        # Define callback functions
        def callback_1(message, topic):
            received_messages_1.append(message)

        def callback_2(message, topic):
            received_messages_2.append(message)

        # Subscribe both callbacks to the same topic
        x.subscribe(topic, callback_1)
        x.subscribe(topic, callback_2)

        # Publish the first value
        x.publish(topic, values[0])

        # Give Redis time to process the message if needed
        time.sleep(0.1)

        # Verify both callbacks received the message
        assert len(received_messages_1) == 1
        assert received_messages_1[0] == values[0]
        assert len(received_messages_2) == 1
        assert received_messages_2[0] == values[0]


@pytest.mark.parametrize("pubsub_context, topic, values", testdata)
def test_pubsub_unsubscribe(pubsub_context, topic, values):
    def publisher_fork():
        with pubsub_context() as pubsub:
            for value in values:
                pubsub.publish(topic, value)
                time.sleep(0.1)

    received = []

    def receive_msg(msg, topic):
        received.append(msg)

    with pubsub_context() as pubsub:
        unsubscribe = pubsub.subscribe(topic, receive_msg)

        pub_process = multiprocessing.Process(target=publisher_fork)
        pub_process.start()

        timeout = 1.0
        start = time.time()
        while len(received) == 0 and (time.time() - start) < timeout:
            time.sleep(0.01)

        unsubscribe()
        pub_process.join(timeout=timeout)

    assert len(received) < len(values)
    assert received[0] == values[0]  # Verify we received the first message


@pytest.mark.parametrize("pubsub_context, topic, values", testdata)
@pytest.mark.asyncio
async def test_async_iterator(pubsub_context, topic, values):
    """Test that async iterator receives messages correctly."""
    with pubsub_context() as x:
        # Get the messages to send (using the rest of the values)
        messages_to_send = values[1:] if len(values) > 1 else values
        received_messages = []

        # Create the async iterator
        async_iter = x.aiter(topic)

        # Create a task to consume messages from the async iterator
        async def consume_messages():
            try:
                async for message in async_iter:
                    received_messages.append(message)
                    # Stop after receiving all expected messages
                    if len(received_messages) >= len(messages_to_send):
                        break
            except asyncio.CancelledError:
                pass

        # Start the consumer task
        consumer_task = asyncio.create_task(consume_messages())

        # Give the consumer a moment to set up
        await asyncio.sleep(0.1)

        # Publish messages
        for msg in messages_to_send:
            x.publish(topic, msg)
            # Small delay to ensure message is processed
            await asyncio.sleep(0.1)

        # Wait for the consumer to finish or timeout
        try:
            await asyncio.wait_for(consumer_task, timeout=1.0)  # Longer timeout for Redis
        except asyncio.TimeoutError:
            consumer_task.cancel()
            try:
                await consumer_task
            except asyncio.CancelledError:
                pass

        # Verify all messages were received in order 2
        assert len(received_messages) == len(messages_to_send)
        assert received_messages == messages_to_send
