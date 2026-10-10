# Provider-agnostic LLM adapter for the Gothic NPC server: API keys + consumer subscriptions (ChatGPT, Claude, Gemini), "gpt-6.1-sol", proxies, ToS

Research date: 2026-10-10 (all "as of" dates refer to this day unless stated). Scope: OpenAI / Anthropic / Google state as of Oct 2026; open-source gateways with 2026 activity.

Notation:
- **[verified]**: checked in a primary source during this research. That means repo source code or README (via `gh`), an official doc page fetched in full, or an official forum announcement.
- **[measured]**: I ran it locally on this Mac.
- **[snippet]**: only from a search-engine summary of an official page. developers.openai.com, help.openai.com and openai.com all returned HTTP 403 (Cloudflare) to fetch and curl, so most OpenAI first-party claims are in this class. Where a GitHub issue quotes the page verbatim, this is noted.
- **[community-verified]**: a third-party developer states they tested it live against the real service (date given).
- **[secondary]**: a news or blog article.
- **[inference]**: my own reasoning.
- **[unverified]**: a claim seen but not confirmed.

---

## TL;DR

1. **"gpt-6.1-sol" is a real OpenAI model.**
   - Basics: GPT-6.1 Sol was released **2026-09-29** at DevDay. API model ID: `gpt-6.1-sol`. Price: $2 in / $10 out per 1M tokens. Context: 1.05M tokens; max output 128K.
   - Subscription access: in **ChatGPT Work and Codex** for Plus, Pro, Business, Enterprise and Edu. It is **not** in regular ChatGPT chat.
   - Latency: **reasoning cannot be turned off**. The minimum effort is `low`; `none` and `minimal` are rejected. Artificial Analysis measures **≈3.0 s time-to-first-answer-token at `low`** on the API. That misses a 2 s "first words" budget on its own.
   - Tools: function tools work **only on the Responses API**. Chat Completions rejects tools for this model.
   - Ultrafast: a faster "Ultrafast" tier launched 2026-10-08. On subscriptions it is only on the Pro 500 plan; on the API it costs $12/$60.
2. **There is now an official way to use a ChatGPT subscription from your own app: "Sign in with ChatGPT" (SIWC) for open-source and local apps**, launched 2026-09-29.
   - Flow: OAuth PKCE with dynamic client registration. You get a bearer token and send it to the normal `https://api.openai.com/v1/responses` with `store:false, stream:true`.
   - Client-side function tools work. Several sampling and limit fields are rejected.
   - Usage is drawn from the user's Plus/Pro allowance.
   - For a Python asyncio server this is just the official `openai` SDK with a different bearer token. **It is the right "subscription backend" to try gpt-6.1-sol first.**
3. **The Codex app-server** (official, persistent JSON-RPC over stdio) also works with a ChatGPT login.
   - It streams deltas and supports client-defined "dynamic tools" (experimental).
   - Local overhead I measured: spawn+initialize ≈115–225 ms, `thread/start` ≈20–90 ms.
   - Downside: it is a coding-agent harness that you have to strip down.
   - OpenAI says app-server auth may still be used by local/open-source apps, but recommends migrating to SIWC.
4. **`codex exec` per request is unsuitable** for live dialogue. It spawns a process per call, and its `--json` output emits the assistant message only when the message is complete (no token deltas) [verified source].
5. **Unofficial proxies** (ChatMock, openai-oauth, CLIProxyAPI's Codex backend, LiteLLM `chatgpt/`, LangChain `chatgpt_oauth`) **reuse Codex's OAuth client ID against `chatgpt.com/backend-api/codex`**. That endpoint is unpublished for third-party apps. They work, but this is impersonation of Codex. Since SIWC exists there is no reason to depend on them.
6. **Anthropic**
   - Prohibited: extracting Claude.ai OAuth tokens into proxies or other apps. Current legal page: developers "may not collect, store, or intermediate Claude.ai credentials".
   - Currently allowed: the unmodified `claude` CLI / Claude Agent SDK signed in with your own plan draws from subscription limits (support article updated ~2026-10-07).
   - Cleanest for a custom server: **Max plans have included $100/$200 per month of real Claude API credits since 2026-10-07** (Pro excluded). Use a normal API key with the official SDK.
7. **Google**
   - Using Gemini CLI or Antigravity OAuth from third-party software is explicitly a ToS violation, and Google enforced it with account bans in Feb 2026.
   - Consumer AI Pro/Ultra login to Gemini CLI was **discontinued 2026-06-18**.
   - Compliant route: **AI Pro and Ultra include $10/$100 per month of Google Cloud credits** usable with a Gemini API key.
8. **Recommendation:** write our own thin async adapter interface (a few hundred lines per backend) on top of the official `openai` and `anthropic` SDKs.
   - Backends:
     - OpenAI **Responses**, where the API key or SIWC token is the only difference;
     - Anthropic Messages;
     - generic OpenAI-compatible Chat Completions for Ollama / mlx-lm / llama.cpp / OpenRouter / Gemini-compat;
     - optional Codex app-server.
   - Do **not** make LiteLLM or CLIProxyAPI a required sidecar. At most, use the LiteLLM or any-llm SDK as a pinned "long-tail" backend.
   - To meet the 2 s budget, route first words to a fast non-reasoning or local model, or hedge requests. Use gpt-6.1-sol for slower or deeper turns.

---

## Q1. What is "gpt-6.1-sol"?

### Takeaway
It exists. It is the mid-tier model of OpenAI's GPT-6 family. The "Sol" name comes from the GPT-6 naming scheme: Astra is the flagship, Sol the middle tier, Luna the small model, and "Instant" the ChatGPT chat model. It is good at agentic and coding work, but its mandatory reasoning makes it slow to the first token for live NPC barks.

### Cited Findings
**Release and family**
- GPT-6 Astra: limited preview 2026-09-03, public 2026-09-04.
- GPT-6 Sol and GPT-6 Luna: 2026-09-22.
- GPT-6 Instant: 2026-10-07 for paid users, 2026-10-08 for free users. — [Wikipedia: GPT-6](https://en.wikipedia.org/wiki/GPT-6) [secondary]
- **GPT-6.1 Sol** was released **2026-09-29**, at DevDay 2026, as an upgrade to GPT-6 Sol. OpenAI's system card PDF is dated 2026-09-29.
  - Positioning: "nearly matches GPT-6 Astra's intelligence on agentic coding, computer use, and professional work at one-fifth of Astra's standard input and output token prices".
  - Sources: [OpenAI: Introducing GPT-6.1 Sol](https://openai.com/index/introducing-gpt-6-1-sol/) [snippet; page 403]; [System card PDF](https://cdn.openai.com/pdf/38e3efcf-545e-44cd-99ec-2b7eb395f4cc/oai_GPT_6_1_Sol.pdf) [snippet]
- OpenAI staff announced it on the developer forum on 2026-09-30: "$2/M input and $10/M output pricing", cached input "$0.10/M" (half of GPT-6 Sol's $0.20). — [OpenAI Community announcement](https://community.openai.com/t/gpt-6-1-sol-in-the-api-a-meaningful-step-up-in-cost-performance/1402388) [verified]
- Astra's API price is $10/$50 per 1M tokens. — [ProPakistani 2026-09-30](https://propakistani.pk/2026/09/30/gpt-6-1-sol-brings-astra-level-performance-at-5x-lower-cost/) [secondary]

**Where it is available**
- **API**: model ID `gpt-6.1-sol`; a dated snapshot `gpt-6.1-sol-2026-09-29` exists in the Foundry catalog. — [truefoundry/models issue #4431, 2026-10-06](https://github.com/truefoundry/models/issues/4431) [verified issue text]
- **ChatGPT Work + Codex**: "available to Plus, Pro, Business, Enterprise, and Edu users through ChatGPT Work and Codex, but is not yet available in regular ChatGPT conversations". — [ProPakistani](https://propakistani.pk/2026/09/30/gpt-6-1-sol-brings-astra-level-performance-at-5x-lower-cost/) [secondary]; [layer3labs guide](https://www.layer3labs.io/guides/how-to-use-gpt-6-1-sol) ("Standard ChatGPT Chat does not route queries to GPT-6.1 Sol at this time") [secondary]; [OpenAI help: ChatGPT Work and Codex](https://help.openai.com/en/articles/20001275-chatgpt-work-and-codex) [snippet]
- Codex is included in the ChatGPT plan. "Every plan shares one usage allowance between Codex and ChatGPT Work". As of Sept 2026, Free and Go do not include Codex. — [developersdigest](https://www.developersdigest.tech/blog/codex-usage-limits-pricing-2026), [SSD Nodes](https://www.ssdnodes.com/learn/codex-with-a-chatgpt-subscription) [secondary]
- **Through Sign in with ChatGPT (subscription)**:
  - `gpt-6.1-sol` appears in the plan's model catalog (`GET /v1/models` with the SIWC token). That catalog listed "ten models … including `gpt-reserve`, `gpt-5.5`, and `codex-auto-review`".
  - The catalog reports per-model reasoning levels, and "`gpt-6.1-sol` reaches `ultra`". — [linrf/dsh-llm-siwc README, "Verified against the live service on 2026-10-08"](https://github.com/linrf/dsh-llm-siwc) [community-verified]
  - Whether the plan route accepts `none` for gpt-6.1-sol is **unverified**. The API does not (see below).
- Also on Azure AI Foundry and Amazon Bedrock model cards, and on OpenRouter as `openai/gpt-6.1-sol`. — [Azure catalog](https://ai.azure.com/catalog/models/gpt-6.1-sol), [AWS Bedrock model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-openai-gpt-6-1-sol.html), [DataCamp](https://www.datacamp.com/blog/gpt-6-1-sol) [snippet/secondary]

**API constraints that matter for NPCs**
- OpenAI's model page, quoted verbatim in a GitHub issue: "`reasoning.effort` supports `low`, `medium` (default), `high`, `xhigh`, and `max`. The `none` and `minimal` reasoning efforts are not supported. Use the Responses API for tool calling. Chat Completions is supported without tool calling." — [truefoundry/models#4431](https://github.com/truefoundry/models/issues/4431) quoting [developers.openai.com/api/docs/models/gpt-6.1-sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) [verified quote]
- The live error when tools are sent to Chat Completions: `Function tools with reasoning_effort are not supported for gpt-6.1-sol in /v1/chat/completions. To use function tools, use /v1/responses or set reasoning_effort to 'none'.`
  - That project also found that its OpenAI-compatible client **silently dropped tools on retry**, which is a gateway/translation-fidelity hazard. — [open-dungeon-master#129, 2026-10-07](https://github.com/Lebbitheplow/open-dungeon-master/issues/129) [verified]
- GPT-6 Sol and GPT-6 Luna (6.0) do accept `reasoning_effort:"none"`, and with it they allow tools on Chat Completions. GPT-6.1 Sol and Astra do not. — same issue [verified]; [GPT-6 Luna model page](https://developers.openai.com/api/docs/models/gpt-6-luna) [snippet]
- Long-context surcharge: prompts above 272K tokens cost 2× input and 1.5× output. Batch/Flex is 50% off; "Fast" is 2×. — [DataCamp](https://www.datacamp.com/blog/gpt-6-1-sol) [secondary]

**Latency** (Artificial Analysis, OpenAI API, fetched 2026-10-10; these numbers drift between snapshots)

| Model / effort | Time to first answer token | Output tok/s | Source |
|---|---|---|---|
| GPT-6.1 Sol `low` | **3.04 s** (peer median 3.80 s) | ~49–51 | [AA](https://artificialanalysis.ai/models/gpt-6-1-sol-low) [verified] |
| GPT-6.1 Sol `high` | ~62 s | ~53 | [AA](https://artificialanalysis.ai/models/gpt-6-1-sol-high) [snippet] |
| GPT-6.1 Sol `max` | ~291 s | ~57–64 | [AA](https://artificialanalysis.ai/models/gpt-6-1-sol) [snippet] |
| GPT-6 Luna non-reasoning (`none`) | **0.77–0.83 s** | 118–126 | [AA](https://artificialanalysis.ai/models/gpt-6-luna-non-reasoning) [verified] |
| GPT-6 Sol non-reasoning (`none`) | 0.94 s | 88–94; AA marks it "deprecated" | [AA](https://artificialanalysis.ai/models/gpt-6-sol-non-reasoning) [verified] |

- **Ultrafast**, announced 2026-10-08:
  - Speed: "Near-Astra intelligence at up to 8x faster speeds than Sol Standard". Price: "$12 per million input tokens and $60 per million output tokens".
  - Codex config: `service_tier = "ultrafast"`.
  - Subscription access: "In Codex and ChatGPT Work, access is available on Pro 500, eligible usage-based Enterprise, and credit-based Edu plans". A user reports Pro 200 does not get it.
  - No TTFT figure is given. — [OpenAI Community announcement](https://community.openai.com/t/ultrafast-is-rolling-out-today-for-gpt-6-1-sol-in-the-api-codex-and-chatgpt-work/1404475) [verified]
  - Up to "300 tokens per second in Codex". — [ProPakistani on Pro 500](https://propakistani.pk/2026/09/30/chatgpt-gets-500-pro-plan-as-openai-halves-pro-200-limits/) [secondary]
- **Plans (DevDay 2026-09-29):**
  - Pro has three tiers: Pro 100, Pro 200 and Pro 500.
  - Pro 500 is "25 times the ChatGPT Plus allowance" and is the only plan with Ultrafast.
  - New Pro 200 subscriptions get a lower allowance; existing subscribers move to it after 2026-10-29. — [ProPakistani](https://propakistani.pk/2026/09/30/chatgpt-gets-500-pro-plan-as-openai-halves-pro-200-limits/) [secondary]
- **Streaming + tool calling**: both supported on the Responses API. This is the standard Responses surface; tool calling was also checked live through SIWC by dsh-llm-siwc, see Q2. [verified/community-verified]

**Closest real names** (in case the user meant something else):
- `gpt-6-sol` (6.0, Sept 22; accepts `none`);
- `gpt-6-luna` (small, fast; `none` supported);
- `gpt-6-astra` (flagship);
- "GPT-6 Instant" (ChatGPT only; the API's `chat-latest` is a moving pointer to the current ChatGPT model, updated Oct 7). — [sahuagin/mu#746](https://github.com/sahuagin/mu/issues/746) [verified issue text]
- Older: GPT-5.6 Sol/Luna/"terra" and `gpt-5.5`, which is still in the SIWC catalog.

**[inference] What this means for NPCs:** gpt-6.1-sol at `low` will typically show first words after about 3 s on the API. That is fine for quest generation, planning and "think, then speak" turns, but over budget for instant barks. For instant barks, use `gpt-6-luna` with `none` (~0.8 s), Claude Haiku 4.5 / Sonnet 5.5 with thinking off, or a local MLX/Ollama model. Alternatively, mask the delay with a scripted acknowledgement line or animation. Ultrafast speeds up token generation, but whether it shortens the reasoning phase before the first token is not documented.

---

## Q2. Using a ChatGPT subscription programmatically

### Takeaway

| Route | Official? | Auth | Transport | Token streaming | Custom function tools | Overhead |
|---|---|---|---|---|---|---|
| **Sign in with ChatGPT (open-source/local)** → `api.openai.com/v1/responses` | **Yes**; launched 2026-09-29, "preview" | OAuth PKCE, dynamic client registration, scope `chatgpt.tokens.use.direct` | HTTPS SSE; `stream:true` and `store:false` required | Yes | **Yes**, client-side function tools; hosted tools no | No extra hop: same endpoint as the API |
| **Codex app-server** (`codex app-server`, or the official `openai-codex` Python SDK) | Yes (Codex product surface); for apps, "continue … migration to SIWC recommended" | ChatGPT login (or a SIWC token, or an API key) | Persistent JSON-RPC over stdio, WebSocket (experimental) or Unix socket | Yes (`item/agentMessage/delta`) | Yes, via `dynamicTools` (**experimental**) → server-to-client `item/tool/call` | Spawn once (≈0.1–0.2 s [measured]), then a Codex harness prompt per turn unless stripped |
| **`codex exec`** per request | Yes | ChatGPT login / `CODEX_API_KEY` | New process per call, JSONL events | **No.** `--json` emits the agent message only on `item.completed` | No dynamic tools from the CLI | Process spawn + config/auth/MCP startup + full harness per request |
| **OpenAI Agents SDK** (`openai-agents` v0.23.1, 2026-10-02) | SDK yes; SIWC support not built in | API key; a SIWC token should work as bearer [inference] | Responses / Chat Completions | Yes | Yes | None beyond the API |
| **Unofficial Codex-OAuth proxies** (ChatMock, openai-oauth, CLIProxyAPI, LiteLLM `chatgpt/`, LangChain `chatgpt_oauth`) | **No.** They reuse the Codex CLI client ID against `chatgpt.com/backend-api/codex` | Codex OAuth / device code | Local HTTP proxy | Yes | Yes (backend accepts tools) | +1 local hop; some inject Codex's own system prompt |

### Cited Findings
**Sign in with ChatGPT (SIWC): the official route for our use case**
- Announcement, DevDay 2026-09-29: Plus/Pro users can use their plan allowance in supported third-party apps.
  - Launch apps include OpenCode, OpenClaw, Pi, T3, Amp, Devin, Warp, Notion, Vercel and Kilo Code.
  - "Usage in external apps is drawn from the same allowance shared with ChatGPT Work and Codex. Connecting an app does not add a new allowance." Per-app weekly caps are set under Settings → Usage, "a cap, not a separate pool".
  - Sources: [XenoSpectrum](https://xenospectrum.com/en/chatgpt-sign-in-subscription-apps/), [eesel.ai](https://www.eesel.ai/blog/sign-in-with-chatgpt) [secondary]; [OpenAI help: Using your ChatGPT plan in other apps](https://help.openai.com/en/articles/20001542-using-your-chatgpt-plan-in-other-apps-and-sites) [snippet]
- **Eligibility**: "At launch, ChatGPT plan usage is available to open-source projects, personal projects that run locally, and selected private apps". Commercial or hosted apps must apply through an [interest form](https://openai.com/form/sign-in-with-chatgpt-interest/). — [OpenAI cookbook: Integrating Sign in with ChatGPT in your Opensource App](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt) [snippet]; [flaviocopes 2026-10-02](https://flaviocopes.com/sign-in-with-chatgpt/) [secondary]. **Our hobby mod running locally fits** [inference].
- **Protocol**, verified in OpenAI's own DevKit source `openai/sign-in-with-chatgpt-devkit` (repo created 2026-09-29, last commit 2026-10-07):
  - issuer `https://auth.openai.com`;
  - first sign-in sends `client_id=dynamic_agent_client`, and the callback returns the issued `client_id`;
  - scopes `openid profile email offline_access resource.invoke chatgpt.tokens.use.direct`;
  - `resource=https://api.openai.com/v1`, PKCE S256 and a loopback callback on `127.0.0.1`;
  - optional `ext_agent_host_id`;
  - inference via the official `openai` Node SDK: `client.responses.create({model, input, instructions, store:false, stream:true})` against `https://api.openai.com/v1`;
  - models via `GET https://api.openai.com/v1/models`, which returns a `models[]` catalog with `slug`, `display_name` and `visibility`. — [GitHub: openai/sign-in-with-chatgpt-devkit](https://github.com/openai/sign-in-with-chatgpt-devkit) `packages/local/src/{oauth,responses,models}.ts` [verified]
- The DevKit code (Node/React) is under the "Sign-in with ChatGPT DevKit **Noncommercial** License v1.0". That covers the DevKit code, not the protocol itself [verified LICENSE; scope is my inference].
- **Request restrictions** (official "Preview limitations" page; text via snippets and repos citing it):
  - `store:false` and `stream:true` are required.
  - Explicit `{type:"message", role:"system"}` items are rejected; use `instructions` or developer messages.
  - About 15 fields are unsupported, including `temperature`, `top_p`, `max_output_tokens`, `metadata`, `prompt`, `truncation` and `user`.
  - Hosted tools are not supported: image generation, file search, Code Interpreter, computer use, hosted MCP/connectors, `tool_search`.
  - **"Function/custom tools are supported and can be grouped in namespaces or supplied through `additional_tools` input items."**
  - Sources: [Preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations) [snippet]; [SurfSense ADR 0050, 2026-10-07](https://github.com/MODSetter/SurfSense) ("function tools are taken when grouped in a namespace") [verified ADR text]
  - Note: SurfSense's earlier ADR 0038 (2026-10-02) believed tools were refused, then corrected itself. The docs were changing in the first week.
- **Live checks by others**: "Client-side function tools work. A full tool-call → tool-result → final-answer round trip was verified live." Also from the same tests:
  - `previous_response_id` over HTTP is rejected, so full history must be replayed.
  - A reasoning summary arrives only when the request carries no tools.
  - Access tokens last 1 h and refresh tokens 30 days; refresh tokens rotate, so refreshes must be serialized.
  - A 429 is `subscription_sharing_usage_limit_exceeded`; do not retry it.
  - `service_tier:"priority"` was ~25% faster end to end (median 17.7 s → 13.2 s on their agentic prompt), but the response echoes `"auto"`. — [linrf/dsh-llm-siwc, 2026-10-08](https://github.com/linrf/dsh-llm-siwc) [community-verified]
- "OpenAI's SIWC resource contract currently does not support a custom API proxy". Requests must go to `api.openai.com`. — [lutzkind/open-webui-chatgpt-siwc README](https://github.com/lutzkind/open-webui-chatgpt-siwc) [verified README]
- **Limits**: on Plus, "the five-hour limit is shared by every app that uses your plan". On Pro, "the five-hour limit doesn't apply to these apps. The weekly limit still does." — [flaviocopes](https://flaviocopes.com/sign-in-with-chatgpt/) [secondary]
- **Python implementations to learn from** (there is no official Python SIWC package):
  - `existence-master/Sentient`, file `sentient/llm/chatgpt.py`: ~18 KB of asyncio/httpx code covering PKCE, refresh, ID-token checks, model listing and SSE; the licence is unclear (GitHub reports NOASSERTION).
  - `MODSetter/SurfSense`: Python, ADRs 0038 and 0050.
  - `lutzkind/open-webui-chatgpt-siwc`: MIT Python adapter exposing an OpenAI-compatible Responses endpoint.
  - GitHub code search for `dynamic_agent_client` (2026-10-10) also finds the flow in OpenClaw, OpenCode, Pi, t3code, TanStack AI, big-AGI, ServiceStack and midscene. [verified]

**Codex app-server**
- What it is: the JSON-RPC 2.0 service behind Codex's rich clients (the VS Code extension and the app).
  - Transports: stdio JSONL (default), WebSocket (experimental) and Unix socket.
  - Primitives: `initialize`, `thread/start|resume|fork`, `turn/start|steer|interrupt`, `model/list`.
  - Streaming notifications: `turn/started`, `item/started`, `item/agentMessage/delta`, `item/completed`, `turn/completed`.
  - Experimental APIs are gated by `capabilities.experimentalApi`. — [Codex App Server docs](https://learn.chatgpt.com/docs/app-server) [verified]; [OpenAI: Unlocking the Codex harness](https://openai.com/index/unlocking-the-codex-harness/) [snippet]
- **Dynamic tools**:
  - `thread/start.dynamicTools` is marked `#[experimental("thread/start.dynamicTools")]`.
  - Spec: `{type:"function", name, description, inputSchema}` or a `namespace` grouping.
  - When the model calls one, the server sends the client a request `item/tool/call` with `{callId, turnId, namespace?, tool, arguments}`. The client replies `{contentItems:[{type:"inputText",text}], success}`. — `codex-rs/protocol/src/dynamic_tools.rs`, `codex-rs/app-server-protocol/src/protocol/common.rs` in [openai/codex](https://github.com/openai/codex) (main, 2026-10-10) [verified]
- **Official Python SDK** `openai-codex` (in `sdk/python` of openai/codex; Apache-2.0; Python ≥3.10):
  - `AsyncCodex` has `thread_start(base_instructions=…, developer_instructions=…, ephemeral=…, model=…, sandbox=…)`.
  - `turn(..., effort=…, output_schema=…, service_tier=…)` returns an `AsyncTurnHandle` with `.stream()`. "One `AsyncCodex` instance can stream multiple active turns concurrently".
  - Login: `login_chatgpt()`, `login_chatgpt_device_code()`, `login_api_key()`.
  - The high-level API docs do not mention dynamic tools, so raw JSON-RPC may be needed for them [verified docs; gap is my inference].
- Codex release cadence: latest stable `rust-v0.162.1` (2026-10-09), with alphas daily. The local install is `codex-cli 0.154.0`, which is behind. [verified]
- **Making it a plain chat engine**: Codex's TUI has a "temporary structured request" mode that "start[s] an ephemeral thread without widening permissions or exposing tools and environment access". It does this by overriding config:
  - `features.shell_tool=false`, `features.unified_exec=false`, `features.multi_agent=false`, `features.memories=false`, `features.standalone_web_search=false`, `features.plugins=false`, `tools.update_plan.enabled=false`, … plus `ephemeral: true`.
  - This is a ready template for an NPC-dialogue thread. — `codex-rs/tui/src/temporary_structured_request.rs` [verified]
  - Feature keys drift between releases. Regenerate the schema with `codex app-server generate-json-schema` after each upgrade.
- **[measured] on this Mac** (codex-cli 0.154.0, isolated `CODEX_HOME`, no auth, no model call):
  - `codex --version` takes 0.09 s;
  - `codex app-server` spawn → `initialize` response takes 115–225 ms (3 runs);
  - `thread/start` (ephemeral, read-only) takes 22–92 ms.
  - So the per-turn cost of a *persistent* app-server is negligible next to model TTFT. Per-request spawning (exec) adds ≥0.1–0.3 s plus harness startup.
- **OpenAI's position on app-server auth** (SIWC docs): "If you've built a local or open-source application using Codex app-server authentication, you can continue using it, though migration to Sign in with ChatGPT is recommended. App-server authentication has never been permitted for commercial or hosted services."
  - The same docs say an app-server can be pointed at a SIWC token: "Codex app-server can be configured to use an OAuth access token authorized for ChatGPT plan usage". — [SIWC: Codex app-server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server) [snippet]

**`codex exec`**
- In `codex-rs/exec/src/event_processor_with_jsonl_output.rs`, `map_started_item` returns `None` for `AgentMessage`/`Reasoning`, and agent text is emitted only in `item.completed`. JSONL event types: `thread.started`, `turn.started|completed|failed`, `item.started|updated|completed`, `error`. **No token deltas.** [verified source, main 2026-10-10]
- OpenAI's guidance for automation is API keys. ChatGPT-managed auth in CI is "advanced", only for trusted private runners, and requires persisting the rotated `auth.json`. — [Codex docs: CI/CD auth](https://developers.openai.com/codex/auth/ci-cd-auth) [snippet]
- **[inference]** Use `codex exec` only for offline batch jobs (generating dialogue lines or quest text at build time), never for live dialogue.

**OpenAI Agents SDK**
- `openai/openai-agents-python` v0.23.1 (2026-10-02, MIT). It is provider-agnostic (Responses, Chat Completions, plus LiteLLM for 100+ models). It has no SIWC/ChatGPT auth built in, and a user question about ChatGPT OAuth with the SDK went unanswered officially. — [repo](https://github.com/openai/openai-agents-python) [verified metadata]; [X post](https://x.com/cedric_chee/status/2028135697140633952) [snippet]
- **[inference, untested]** Pass `AsyncOpenAI(api_key=<SIWC access token>)` to `OpenAIResponsesModel`, force `store=False` and streaming, and strip unsupported `ModelSettings` (temperature, max_tokens). Disable SDK tracing export, because it would try to upload with an API key. For our server the Agents SDK adds little over calling `responses.create` directly.

**Unofficial proxies** (how they work)
- LiteLLM's `chatgpt` provider constants:
  - `CHATGPT_API_BASE = "https://chatgpt.com/backend-api/codex"`, `CHATGPT_CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"`;
  - `DEFAULT_ORIGINATOR = "codex_cli_rs"`;
  - it uses a device-code flow at `auth.openai.com/api/accounts/deviceauth/*`;
  - default instructions start "You are Codex, based on GPT-5…". — `litellm/llms/chatgpt/common_utils.py` [verified]
- The LangChain helper `langchain_openai.chatgpt_oauth` is "Experimental and unofficial… not an official OpenAI API integration". — [LangChain reference](https://reference.langchain.com/python/langchain-openai/chatgpt_oauth) [verified]
- SurfSense's reasoning for avoiding the Codex route: it "present[s] SurfSense as the Codex CLI on endpoints OpenAI never published for other apps, put[s] users' accounts at the risk of OpenAI enforcing that, hide[s] SurfSense from the per-app caps users set in ChatGPT, and can break with any Codex release". — [SurfSense ADR 0038](https://github.com/MODSetter/SurfSense) [verified]

---

## Q3. Open-source LLM proxies and gateways (API keys + subscriptions)

### Takeaway
No well-known project is literally called **"llmproxy"**. The GitHub repos with that name are all small (≤255★). The mature options are LiteLLM (Python SDK + proxy) and CLIProxyAPI (Go; the most complete subscription coverage).

- Every gateway that "supports subscriptions" does it through **unofficial OAuth reuse**: Codex's client ID, Claude Code OAuth, Gemini CLI/Antigravity OAuth.
- **None of the big gateways implements OpenAI's official SIWC yet.** GitHub code search for `tokens.use.direct` returned 0 hits in LiteLLM and CLIProxyAPI on 2026-10-10.
- For our server, a sidecar gateway adds a hop and protocol-translation risk without solving the subscription problem cleanly.

### Cited Findings
All metadata via `gh api` on 2026-10-10 [verified] unless noted.

| Project | License | Lang | Activity (latest release / push) | Subscription backends (mechanism) | Streaming / tools | macOS self-host |
|---|---|---|---|---|---|---|
| **LiteLLM** ([BerriAI/litellm](https://github.com/BerriAI/litellm)) | MIT core + commercial `enterprise/` dir (GitHub: NOASSERTION) | Python | stable v1.104.2 (2026-10-08); v1.106.0-dev.3 (2026-10-09); ~61k★ | `chatgpt/` via **Codex client ID + device code → chatgpt.com/backend-api/codex** (unofficial); `github_copilot`. Docs list only gpt-5.3/5.4-era models. Note: `codex/` and `claude_code/` "harness" modules run those CLIs *against* LiteLLM; they do not use subscriptions. No SIWC. | Mature for API providers; for `chatgpt/` it strips token-limit fields and metadata; one shared account per proxy | `pip`/`uv` (SDK) or Docker (proxy; Postgres optional). **Supply-chain incident 2026-03-24**: PyPI 1.82.7/1.82.8 were malicious for under 5 h ([FutureSearch](https://futuresearch.ai/blog/litellm-pypi-supply-chain-attack/), [Arthur](https://www.arthur.ai/column/litellm-supply-chain-attack-pypi-compromise-2026)) [secondary]. Pin versions and hashes. |
| **CLIProxyAPI** ([router-for-me/CLIProxyAPI](https://github.com/router-for-me/CLIProxyAPI)) | MIT | Go | v8.0.23 (2026-10-09); ~54.7k★ | Codex OAuth (GPT-6 Astra/6.1 Sol/Luna), Claude Code OAuth, Gemini CLI / AI Studio / Antigravity, Grok Build, Kimi, Meta Muse, Devin; multi-account round-robin. Upstream `chatgpt.com/backend-api/codex/...` [verified code search] | Serves OpenAI (incl. Responses), Gemini and Claude-compatible APIs; streaming, WebSocket, function calling | Go binary, Docker, `brew install cliproxyapi` (seen in community dotfiles [verified code search]), GUI "EasyCLIProxyAPI"; default port 8317. **README sponsors are mostly "relay" resellers of Claude/Codex subscriptions**, an ecosystem built on ToS-violating resale. |
| **claude-code-router (CCR)** ([musistudio/claude-code-router](https://github.com/musistudio/claude-code-router)) | MIT | TypeScript | v3.1.3 (2026-10-09); ~37.6k★ | Mainly API-key providers (OpenAI Chat/Responses, Anthropic, Gemini, OpenRouter, DeepSeek, Kimi, …); "local login import where supported" (code search shows Codex-backend and Claude-OAuth references; not inspected) | Yes; built for coding agents (Claude Code, Codex, OpenCode…) | npm / desktop UI; gateway `127.0.0.1:3456`. A control plane for coding agents, not a library. |
| **ChatMock** ([RayBytes/ChatMock](https://github.com/RayBytes/ChatMock)) | MIT | Python | last release v1.35 (2025-11-26), push 2026-09-01; ~1.6k★ | ChatGPT via Codex OAuth | OpenAI + Ollama-compatible; tools, `/v1/responses` HTTP+WS, fast mode, effort `none…ultra` | `brew tap RayBytes/chatmock && brew install chatmock`, or pipx |
| **openai-oauth** ([EvanZhouDev/openai-oauth](https://github.com/EvanZhouDev/openai-oauth)) | Apache-2.0 | TypeScript | v2.0.0 (2026-07-15), push 2026-09-29; ~1.4k★ | ChatGPT via the local Codex login | `/v1/responses`, `/v1/chat/completions`, `/v1/models`; streaming, tool calls | `npx openai-oauth` → `127.0.0.1:10531`. Its "Sign in with ChatGPT" button is its own browser extension, **not** OpenAI's SIWC. |
| **sub2api** ([Wei-Shaw/sub2api](https://github.com/Wei-Shaw/sub2api)) | LGPL-3.0 | Go | v0.2.15 (2026-10-09); ~43.6k★ | Claude/OpenAI/Gemini/Grok subscriptions; description promotes "拼车共享" (carpool sharing of accounts) | — | **Avoid**: account sharing and resale. |
| **Bifrost** ([maximhq/bifrost](https://github.com/maximhq/bifrost)) | Apache-2.0 | Go | transports/v2.2.6 (2026-10-06); ~8.7k★ | API keys; code search shows `chatgpt.com/backend-api/codex` references (not inspected) | Yes | Go binary / Docker |
| **Portkey gateway** ([Portkey-AI/gateway](https://github.com/Portkey-AI/gateway)) | MIT | TypeScript | v1.15.2 (2026-01-12), push 2026-05-25 (slowing) | API keys only | Yes | npx / Docker |
| **OpenRouter** (hosted) | n/a | n/a | — | API credits + BYOK; no consumer subscriptions | Yes; has `openai/gpt-6.1-sol` [secondary] | Hosted. Self-reported ~40 ms overhead ([docs](https://openrouter.ai/docs/features/latency-and-performance)); an independent benchmark measured +207–246 ms ([Edgee](https://www.edgee.ai/blog/posts/i-benchmarked-six-ai-gateways-including-ours)) [snippet] |
| "**llmproxy**" (name search) | various | — | [ultrasev/llmproxy](https://github.com/ultrasev/llmproxy) 255★ (Cloudflare-Worker reverse proxy for OpenAI/Gemini/Groq keys; push 2026-04-30; no license); [fabriziosalmi/llmproxy](https://github.com/fabriziosalmi/llmproxy) MIT Python 12★ (security gateway, 24 providers); [BillJr99/llmproxy](https://github.com/BillJr99/llmproxy), [dkyg1087/llmproxy](https://github.com/dkyg1087/llmproxy), [coral-li/llmproxy](https://github.com/coral-li/llmproxy), [Mo7ammedd/LLMProxy](https://github.com/Mo7ammedd/LLMProxy) (all ≤5★); [obirler/LLMProxy](https://github.com/obirler/LLMProxy) C# 29★ (stale since 2025-12) | None support subscriptions | — | Not recommended; "LLM proxy" is most likely meant generically (LiteLLM Proxy is the usual referent). Nearest Python library-style alternative: [Nayjest/lm-proxy](https://github.com/Nayjest/lm-proxy), MIT, 154★, FastAPI, push 2026-10-01. |

**SIWC-based adapters (official subscription route)**
- [openai/sign-in-with-chatgpt-devkit](https://github.com/openai/sign-in-with-chatgpt-devkit): Node; noncommercial license; created 2026-09-29.
- [lutzkind/open-webui-chatgpt-siwc](https://github.com/lutzkind/open-webui-chatgpt-siwc): MIT; Python; created 2026-10-08; OpenAI-compatible Responses endpoint; Docker plus a reverse proxy, built for Open WebUI.
- [linrf/dsh-llm-siwc](https://github.com/linrf/dsh-llm-siwc): MIT; TypeScript.
- All three are brand new, 0–42★.

**Python building blocks (for writing our own adapter)**
- [openai/openai-python](https://github.com/openai/openai-python): v3.28.0 (2026-10-09), Apache-2.0.
- [anthropics/anthropic-sdk-python](https://github.com/anthropics/anthropic-sdk-python): v1.13.0 (2026-10-09), MIT.
- [mozilla-ai/any-llm](https://github.com/mozilla-ai/any-llm): 1.33.0 (2026-10-09), Apache-2.0; thin unified SDK over official provider SDKs.
- [pydantic/pydantic-ai](https://github.com/pydantic/pydantic-ai): MIT, very active.
- [andrewyng/aisuite](https://github.com/andrewyng/aisuite): v0.1.3 (2026-07-20), MIT.
- `openai-codex` (official Codex Python SDK).
- **Local runtimes**:
  - [ollama](https://github.com/ollama/ollama) v0.40.2 (2026-10-08). The local client here is **0.24.0, which is outdated**.
  - [mlx-lm](https://github.com/ml-explore/mlx-lm) v0.31.3 (2026-04-22).
  - [llama.cpp](https://github.com/ggml-org/llama.cpp) b11552 (2026-10-10).
  - All three expose OpenAI-compatible endpoints [verified metadata].

---

## Q4. Terms of service (as of 2026-10-10): personal hobby use of consumer subscriptions via third-party apps or proxies

### Takeaway

| Vendor | Allowed (personal) | Prohibited / risky | Enforcement history |
|---|---|---|---|
| **OpenAI** | **SIWC** in open-source/local personal apps (self-serve). Codex CLI/app/app-server with your own login; app-server auth tolerated for local/open-source apps, SIWC recommended. | Commercial or hosted use without approval. Account sharing or resale. ToU bans programmatic extraction and circumventing limits. Codex-client-ID proxies use an endpoint not published for other apps: grey zone, no explicit public ban found. | No documented campaign against personal Codex-OAuth proxy users. Forum reports of a suspension wave around 2026-06-25/26 were attributed by users to a fraud-detection fault [unverified]. |
| **Anthropic** | Unmodified `claude` binary / Claude Agent SDK with your own Pro/Max login (draws from subscription limits; announced split "paused"). **Max/Team: monthly API credits** via a normal API key (since 2026-10-07). | Using Claude.ai OAuth tokens in other products, proxies or gateways. "May not collect, store, or intermediate Claude.ai credentials". Offering Claude.ai login in your own app. | 2026-01-09: blocking began. 2026-02-19/20: docs clarified. 2026-04-04: all third-party harnesses blocked. 2026-05-13: reversed. 2026-06-15: credit plan paused. |
| **Google** | Gemini API with a key; AI Pro/Ultra include $10/$100 per month of Cloud credits. Official Gemini CLI/Antigravity clients only. | "Directly accessing the services powering Gemini CLI … using third-party software … (e.g. using OpenClaw with Gemini CLI OAuth) is a violation". Antigravity ToS bars third-party tools and proxies. | Feb 2026 bans of AI Pro/Ultra users (no refunds), reversed 2026-02-27. Detection tightened from 2026-03-25. Consumer Gemini CLI login ended 2026-06-18. |

### Cited Findings
**OpenAI**
- SIWC eligibility and the app-server statement: see Q2.
  - Sources: [SIWC overview](https://developers.openai.com/siwc/token-sharing-open-source), [SIWC: Codex app-server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server), [Sign in with ChatGPT help article](https://help.openai.com/en/articles/20001410-sign-in-with-chatgpt), [Using your ChatGPT plan in other apps](https://help.openai.com/en/articles/20001542-using-your-chatgpt-plan-in-other-apps-and-sites) [snippet]
  - The help article says that "If you have a Go, Plus, or Pro plan, you can also choose to use your ChatGPT plan for AI requests in participating commercial apps". Open-source plan usage is described as Plus/Pro. [snippet]
- OpenAI Terms of Use: users may not "automatically or programmatically extract data or Output", or interfere with the Services by "circumventing any rate limits or restrictions or bypassing any protective measures". — [openai.com/policies/terms-of-use](https://openai.com/policies/terms-of-use/) [snippet; page 403]
- **[inference]** SIWC is OpenAI's sanctioned answer to "use my plan in my own tool". Building on the Codex-client-ID backend now carries avoidable risk:
  - it pretends to be Codex;
  - it bypasses the per-app caps;
  - it can break with any Codex release.
- Commercial distribution of our mod, if it ever happens, would need OpenAI's approval for plan usage. The DevKit code licence is also noncommercial.

**Anthropic**
- [Claude Code docs: Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance), fetched in full 2026-10-10 [verified]:
  - "**OAuth authentication** is intended exclusively for purchasers of Claude Free, Pro, Max, Team, and Enterprise subscription plans and is designed to support ordinary use of Claude Code and other native Anthropic applications."
  - "**Developers** building products or services that interact with Claude's capabilities, including those using the Agent SDK, should use API key authentication… Anthropic does not permit third-party developers to offer Claude.ai login into their own applications, or to route requests through Free, Pro, or Max plan credentials on behalf of their users. Moreover, developers may not collect, store, or intermediate Claude.ai credentials or session tokens — sign-in to a Claude account must complete through Anthropic's own flow."
  - "Advertised usage limits for Pro and Max plans assume ordinary, individual usage of Claude Code and the Agent SDK."
  - "Anthropic reserves the right to take measures to enforce these restrictions and may do so without prior notice."
- [Support: Use the Claude Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan), as of ~2026-10-07: "We've paused the previously-announced changes to Claude Agent SDK usage. For now, nothing has changed: Claude Agent SDK, `claude -p`, and third-party app usage still draw from your subscription limits." [verified]
- **Monthly API credits for Max and Team, announced 2026-10-07**:
  - Amounts: Max 5x $100/month; Max 20x $200/month; Team $20 or $100 per seat, capped at $500 per org. **Pro is not eligible.**
  - Coverage: the Messages and Batches API, the Agent SDK and the Playground, but not interactive Claude Code.
  - Claiming: link a Console org in claude.ai billing; there is a 7-day wait for new subscribers; credits do not roll over. — [Support article](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-and-team-plans), [Platform docs](https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers) [via XenoSpectrum, secondary]
- Timeline:
  - 2026-01: enforcement against tools "spoofing the Claude Code harness"; OpenCode removed Claude subscription support citing "anthropic legal requests". — [The Register 2026-02-20](https://www.theregister.com/software/2026/02/20/anthropic-clarifies-ban-on-third-party-tool-access-to-claude/5014546) [secondary]
  - 2026-04-04: OpenClaw and others blocked. — [HN](https://news.ycombinator.com/item?id=47633396), [TNW](https://thenextweb.com/news/anthropic-openclaw-claude-subscription-ban-cost) [secondary]
  - 2026-05-13: reversal plus an announced "Agent SDK credit" from 2026-06-15 ([@ClaudeDevs](https://x.com/ClaudeDevs/status/2054610152817619388), [VentureBeat](https://venturebeat.com/technology/anthropic-reinstates-openclaw-and-third-party-agent-usage-on-claude-subscriptions-with-a-catch)).
  - 2026-06-15: paused on the day it was due ([The New Stack](https://thenewstack.io/anthropic-pauses-claude-agent-sdk-subscription-change/), [HN](https://news.ycombinator.com/item?id=48545980)) [secondary]
- **[inference] For our server:**
  - CLIProxyAPI's Claude-OAuth backend, or any token-extracting proxy, falls under "collect, store, or intermediate", so **don't**.
  - Driving the unmodified `claude` CLI or Agent SDK with your own login is currently tolerated for individual use. It is heavy (a full Claude Code harness per call) and the policy has flip-flopped three times this year.
  - **The Max API credits are the clean path.**

**Google**
- [Gemini CLI: Terms of Service and Privacy](https://github.com/google-gemini/gemini-cli/blob/main/docs/resources/tos-privacy.md), repo text fetched 2026-10-10 [verified]: "Directly accessing the services powering Gemini CLI (for example, the Gemini Code Assist service) using third-party software, tools, or services (for example, using OpenClaw with Gemini CLI OAuth) is a violation of applicable terms and policies. Such actions may be grounds for suspension or termination of your account."
- Gemini CLI team posts:
  - 2026-02-27: "Antigravity bans … specifically the use of 3rd party tools or proxies to access Antigravity resources and quotas". This was followed by a system-wide unban and a recertification flow. — [Discussion #20632](https://github.com/google-gemini/gemini-cli/discussions/20632) [verified]
  - 2026-03-18: "more robust detection for policy-violating use cases (e.g. using Gemini CLI oAuth with third-party software)", plus traffic prioritisation from 2026-03-25. — [Discussion #22970](https://github.com/google-gemini/gemini-cli/discussions/22970) [verified]
- Ban reports: [OpenClaw issue #14203](https://github.com/openclaw/openclaw/issues/14203), [Google AI forum](https://discuss.ai.google.dev/t/urgent-mass-403-tos-bans-on-gemini-api-antigravity-for-open-source-cli-users-paid-tier/124508), [WinBuzzer 2026-02-23](https://winbuzzer.com/2026/02/23/google-bans-ai-subscribers-openclaw-no-refunds-xcxwbn/) [secondary]
- **2026-06-18**: Gemini Code Assist stopped serving the individuals, AI Pro and AI Ultra tiers in the IDE extensions **and Gemini CLI**; "you can no longer use the Login with Google option". Users are told to migrate to Antigravity. — [Google deprecation notice](https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals) [snippet]
- **Developer credits**: since 2026-01-27, AI Pro includes $10/month and AI Ultra $100/month in Google Cloud credits, activated at google.dev ("Activate Developer Benefits") and usable for the Gemini API. — [Google blog](https://blog.google/innovation-and-ai/technology/developers-tools/gdp-premium-ai-pro-ultra/), [Medium/Google Cloud](https://medium.com/google-cloud/from-prototype-to-production-how-google-ai-subscription-bridges-the-last-mile-for-ai-developers-dd80bb149a3e) [snippet]

---

## Q5. Recommended adapter design for the Python asyncio NPC server

### Takeaway
- **Own the interface; borrow the transports.** Define a small streaming-event interface and implement 3 core backends with the official SDKs, plus 1–2 optional ones.
- The subscription backend for "gpt-6.1-sol from the subscription" is **the same code as the OpenAI API-key backend**, with a SIWC token provider and a "plan profile" that strips unsupported fields.
- Do not run a mandatory gateway sidecar.

### Proposed shape [inference]
```python
# events: everything a backend yields
TextDelta(text) | ToolCallStart(call_id, name) | ToolCallArgsDelta(call_id, json_fragment)
| ToolCallDone(call_id, name, args: dict) | Usage(...) | Done(stop_reason) | BackendError(kind, retryable)

@dataclass
class ToolSpec: name: str; description: str; json_schema: dict   # allowlisted NPC action

@dataclass
class ChatRequest:
    instructions: str            # stable persona/world rules first (prompt-cache friendly)
    messages: list[Msg]          # normalized user/assistant/tool_result turns
    tools: list[ToolSpec]
    model: str; effort: str | None
    max_output_tokens: int | None   # best effort; SIWC rejects it, so enforce client-side
    deadline_first_token_s: float   # e.g. 1.5
    session_id: str | None          # cache affinity (SIWC: `session-id` header per dsh-llm-siwc)

class LLMBackend(Protocol):
    caps: Capabilities   # tools?, max_tokens?, temperature?, min_effort, needs_responses_api, ...
    def stream(self, req: ChatRequest) -> AsyncIterator[Event]: ...
```

**Backends**
1. `OpenAIResponsesBackend(auth=ApiKey | SiwcToken)` uses `openai.AsyncOpenAI(...).responses.create(stream=True)`.
   - Plan profile: set `store=False`; move system text to `instructions`; replay full history (no `previous_response_id`); drop `temperature`, `top_p` and `max_output_tokens`; group tools in a namespace if required.
   - Never retry `subscription_sharing_usage_limit_exceeded`.
   - `SiwcToken` is a ~300–500 line async module (PKCE, loopback on 127.0.0.1, stable `ext_agent_host_id`, serialized refresh-token rotation, macOS Keychain storage). Port it from Sentient or SurfSense, or follow the DevKit.
2. `AnthropicBackend` uses `anthropic.AsyncAnthropic().messages.stream(...)` with an API key (Max credits or Console).
   - Tools: `strict: true` with `additionalProperties:false`, and `eager_input_streaming: true` for argument streaming.
   - Forced `tool_choice` `any`/`tool` returns 400 on Opus 5.5, Sonnet 5.5 and Fable 5.1; use `auto` plus instructions.
   - For speed, use `claude-haiku-4-5`, or `claude-sonnet-5-5` with `thinking:{type:"between_tools"}`, which is its way to turn thinking off.
   - Source: the claude-api skill reference cached 2026-09-25 [verified skill doc].
3. `OpenAICompatChatBackend` uses `AsyncOpenAI(base_url=...)` Chat Completions for Ollama, mlx-lm `server`, llama.cpp `llama-server`, OpenRouter and Gemini's OpenAI-compat endpoint.
   - It must **error loudly if tools would be dropped**, the open-dungeon-master#129 failure mode.
4. *(optional)* `CodexAppServerBackend` keeps one persistent `codex app-server` and uses ephemeral threads.
   - Override `base_instructions` and disable features using the `temporary_structured_request.rs` list.
   - Register NPC actions as `dynamicTools` (experimental flag).
   - Map `item/agentMessage/delta` to `TextDelta` and the `item/tool/call` request to `ToolCallDone`, then answer it.
   - Use it only if SIWC turns out to lack a model or effort we need (e.g. Ultrafast, or Codex-only slugs).
5. *(optional, non-realtime)* `ClaudeCodeBackend` via the Claude Agent SDK on your own login, for offline content generation only.

**Routing and latency policy**
- Per call type: bark/greeting, dialogue turn, quest/planning, offline content.
- Hedging: if no `TextDelta` arrives within the deadline, start a fallback backend (local MLX/Ollama or `gpt-6-luna` at `none`) or play a pre-authored filler line, then cancel the loser.
- Keep HTTP/2 connections warm.
- Put stable prefixes (persona, world rules, tool schemas) first for prompt caching.
- Keep the per-NPC tool list small.
- Stream sentence-chunked text to subtitles and TTS.

**Tool safety**
- Validate every tool call against the allowlisted JSON Schema (`jsonschema`/pydantic) before the game sees it. The Anthropic eager-streaming parser can return truncated input.
- Treat model text as untrusted.
- Allow at most one action per turn unless the action is a known-safe composite.

**Lean on vs. write**
- Lean on `openai`, `anthropic` and (optionally) `openai-codex`.
- Write the interface, router, SIWC auth and tool validation ourselves; small, testable, no gateway hop.
- Optionally add `any-llm` (Apache-2.0, built on official SDKs) or the **LiteLLM SDK** as a catch-all backend for exotic providers. Pin exact versions with hashes (LiteLLM PyPI compromise, 2026-03-24).
- Use **CLIProxyAPI or LiteLLM Proxy only as an experiment sidecar**, never for Claude or Gemini OAuth.

### Next steps / open questions to verify by experiment
1. **TTFT on this Mac**, 20 runs each, p50/p90:
   - (a) SIWC `gpt-6.1-sol` at `low` (and any lower level the plan catalog advertises);
   - (b) app-server with the same model, with features stripped;
   - (c) API `gpt-6-luna` at `none`;
   - (d) Claude Haiku 4.5;
   - (e) a local 4–8B model via mlx-lm or Ollama 0.40.
2. Whether the SIWC catalog includes `gpt-6-luna` and which efforts it allows for each model. Read `GET /v1/models` with the token.
3. Whether SIWC requires tools inside a `namespace` (SurfSense ADR 0050) or accepts plain top-level function tools (dsh-llm-siwc). Test both.
4. Plan-limit consumption per NPC line on Plus vs Pro. Per-app caps can be set under ChatGPT Settings → Usage.

---

## Sources (primary first)
- OpenAI:
  - [Introducing GPT-6.1 Sol](https://openai.com/index/introducing-gpt-6-1-sol/)
  - [GPT-6.1 Sol model page](https://developers.openai.com/api/docs/models/gpt-6.1-sol)
  - [Forum: GPT-6.1 Sol in the API (2026-09-30)](https://community.openai.com/t/gpt-6-1-sol-in-the-api-a-meaningful-step-up-in-cost-performance/1402388)
  - [Forum: Ultrafast (2026-10-08)](https://community.openai.com/t/ultrafast-is-rolling-out-today-for-gpt-6-1-sol-in-the-api-codex-and-chatgpt-work/1404475)
  - [SIWC docs](https://developers.openai.com/siwc)
  - [SIWC quickstart](https://developers.openai.com/siwc/quickstart)
  - [SIWC preview limitations](https://developers.openai.com/siwc/token-sharing-open-source/preview-limitations)
  - [SIWC: Codex app-server](https://developers.openai.com/siwc/token-sharing-open-source/codex-app-server)
  - [SIWC cookbook](https://developers.openai.com/cookbook/articles/sign-in-with-chatgpt)
  - [SIWC DevKit repo](https://github.com/openai/sign-in-with-chatgpt-devkit)
  - [Codex App Server docs](https://learn.chatgpt.com/docs/app-server)
  - [openai/codex repo](https://github.com/openai/codex)
  - [Codex CI/CD auth](https://developers.openai.com/codex/auth/ci-cd-auth)
  - [Terms of Use](https://openai.com/policies/terms-of-use/)
- Anthropic:
  - [Legal and compliance](https://code.claude.com/docs/en/legal-and-compliance)
  - [Agent SDK with your Claude plan](https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan)
  - [Monthly API credits for Max and Team](https://support.claude.com/en/articles/17154008-monthly-api-credits-for-max-and-team-plans)
- Google:
  - [Gemini CLI ToS doc](https://github.com/google-gemini/gemini-cli/blob/main/docs/resources/tos-privacy.md)
  - [Discussion #20632](https://github.com/google-gemini/gemini-cli/discussions/20632)
  - [Discussion #22970](https://github.com/google-gemini/gemini-cli/discussions/22970)
  - [Code Assist consumer deprecation](https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals)
  - [AI Pro/Ultra developer benefits](https://blog.google/innovation-and-ai/technology/developers-tools/gdp-premium-ai-pro-ultra/)
- Measurements: [Artificial Analysis: GPT-6.1 Sol low](https://artificialanalysis.ai/models/gpt-6-1-sol-low), [GPT-6 Luna non-reasoning](https://artificialanalysis.ai/models/gpt-6-luna-non-reasoning), [GPT-6 Sol non-reasoning](https://artificialanalysis.ai/models/gpt-6-sol-non-reasoning)
- Community implementations:
  - [linrf/dsh-llm-siwc](https://github.com/linrf/dsh-llm-siwc)
  - [lutzkind/open-webui-chatgpt-siwc](https://github.com/lutzkind/open-webui-chatgpt-siwc)
  - [MODSetter/SurfSense](https://github.com/MODSetter/SurfSense) (ADRs 0038/0050)
  - [existence-master/Sentient](https://github.com/existence-master/Sentient)
  - [truefoundry/models#4431](https://github.com/truefoundry/models/issues/4431)
  - [open-dungeon-master#129](https://github.com/Lebbitheplow/open-dungeon-master/issues/129)
- Gateways: as linked in the Q3 table.
- Secondary/news: as linked inline. Treat the dates in these as reported.
