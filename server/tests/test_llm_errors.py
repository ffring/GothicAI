import httpx
import openai

from gothic_ai.llm.openai_responses import PLAN_LIMIT, api_error

REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")


def test_in_stream_plan_limit_error_becomes_a_clear_non_retryable_error():
    # raised by the SDK while iterating the SSE stream: a bare APIError, not a status error
    err = openai.APIError(
        "The ChatGPT user has reached their Subscription Sharing usage limit. Ask the user to try again "
        "after their usage limit resets or use an API key instead.",
        REQUEST,
        body=None,
    )
    mapped = api_error("chatgpt", err)
    assert PLAN_LIMIT in str(mapped)
    assert not mapped.retryable


def test_connection_errors_are_retryable():
    mapped = api_error("chatgpt", openai.APIConnectionError(request=REQUEST))
    assert mapped.retryable and "connection failed" in str(mapped)


def test_status_errors_keep_the_http_code():
    response = httpx.Response(400, request=REQUEST)
    err = openai.BadRequestError("Unsupported parameter: reasoning", response=response, body=None)
    assert "HTTP 400" in str(api_error("chatgpt", err))
