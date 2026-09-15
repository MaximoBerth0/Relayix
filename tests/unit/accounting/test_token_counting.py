"""Token counters: the money-critical input to pricing. Get this wrong and
every bill is wrong.
"""

import pytest
import tiktoken

from app.core.accounting.anthropic_counter import AnthropicCounter
from app.core.accounting.openai_counter import OpenAICounter
from app.core.accounting.registry import CounterRegistry, build_registry
from app.core.exceptions import CounterNotRegistered
from app.models.domain.chat import ChatRequest, Message
from app.models.domain.enums import ProviderEnum

_ENC = tiktoken.get_encoding("o200k_base")


def request(*messages: Message, model: str = "gpt-4o") -> ChatRequest:
    return ChatRequest(model=model, messages=list(messages))


# AnthropicCounter: a local heuristic, Anthropic ships no tokenizer


def test_anthropic_counter_of_no_messages_is_just_the_reply_overhead():
    counter = AnthropicCounter()

    assert counter.count(request()) == 3  # _TOKENS_PER_REPLY


def test_anthropic_counter_of_an_empty_message_is_just_the_per_message_overhead():
    counter = AnthropicCounter()
    # role "user" is 4 chars -> ceil(4/3.5) = 2, content "" -> 0
    expected = 3 + 2 + 0 + 3  # per-message + role + content + reply overhead

    assert counter.count(request(Message(role="user", content=""))) == expected


def test_anthropic_counter_grows_with_content_length():
    counter = AnthropicCounter()

    short = counter.count(request(Message(role="user", content="hi")))
    long = counter.count(request(Message(role="user", content="hi" * 100)))

    assert long > short


def test_anthropic_counter_sums_every_message():
    counter = AnthropicCounter()
    one = counter.count(request(Message(role="user", content="hello")))
    two = counter.count(
        request(
            Message(role="user", content="hello"),
            Message(role="assistant", content="hello"),
        )
    )

    assert two > one


# OpenAICounter: real tiktoken-based counting


def test_openai_counter_of_no_messages_is_just_the_reply_overhead():
    counter = OpenAICounter()

    assert counter.count(request(model="gpt-4o")) == 3  # _TOKENS_PER_REPLY


def test_openai_counter_matches_tiktoken_plus_overhead_for_a_known_model():
    counter = OpenAICounter()
    message = Message(role="user", content="hello there")
    expected = 3 + len(_ENC.encode("user")) + len(_ENC.encode("hello there")) + 3

    assert counter.count(request(message, model="gpt-4o")) == expected


def test_openai_counter_falls_back_to_o200k_base_for_an_unknown_model():
    """tiktoken.encoding_for_model raises KeyError for a model it doesn't
    recognize, the counter must not propagate that, and must count the same
    either way since gpt-4o also resolves to the o200k_base fallback.
    """
    counter = OpenAICounter()
    message = Message(role="user", content="hello there")

    known = counter.count(request(message, model="gpt-4o"))
    unknown = counter.count(request(message, model="not-a-real-model-xyz"))

    assert known == unknown


def test_openai_counter_grows_with_content_length():
    counter = OpenAICounter()

    short = counter.count(request(Message(role="user", content="hi")))
    long = counter.count(request(Message(role="user", content="hi " * 200)))

    assert long > short


def test_openai_counter_of_empty_content_adds_no_content_tokens():
    counter = OpenAICounter()
    expected = 3 + len(_ENC.encode("user")) + 0 + 3

    assert counter.count(request(Message(role="user", content=""))) == expected


# CounterRegistry


def test_registry_returns_the_counter_registered_for_a_provider():
    registry = CounterRegistry()
    counter = OpenAICounter()
    registry.register(ProviderEnum.OPENAI, counter)

    assert registry.get(ProviderEnum.OPENAI) is counter


def test_registry_raises_for_an_unregistered_provider():
    registry = CounterRegistry()

    with pytest.raises(CounterNotRegistered):
        registry.get(ProviderEnum.ANTHROPIC)


def test_build_registry_wires_a_counter_for_every_provider():
    registry = build_registry()

    assert isinstance(registry.get(ProviderEnum.OPENAI), OpenAICounter)
    assert isinstance(registry.get(ProviderEnum.ANTHROPIC), AnthropicCounter)
