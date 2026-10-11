import pytest

from gothic_ai.llm.base import LLMError
from gothic_ai.llm.siwc import ChatGPTAuth, TokenStore


async def test_sign_in_errors_are_llm_errors(tmp_path):
    with pytest.raises(LLMError, match="login chatgpt"):
        await ChatGPTAuth(TokenStore(tmp_path / "none.json"))()
