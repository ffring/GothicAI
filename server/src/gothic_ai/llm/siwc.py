"""Sign in with ChatGPT for the LLM backends: the shared `chatgpt_auth` package with errors mapped
to `LLMError`, so a missing or expired sign-in shows up like any other backend error."""

from __future__ import annotations

from chatgpt_auth import AuthError, Credentials, TokenStore, login
from chatgpt_auth import ChatGPTAuth as _ChatGPTAuth

from .base import LLMError

__all__ = ["AuthError", "ChatGPTAuth", "Credentials", "TokenStore", "login"]


class ChatGPTAuth(_ChatGPTAuth):
    async def __call__(self) -> str:
        try:
            return await super().__call__()
        except AuthError as err:
            raise LLMError(str(err)) from err
