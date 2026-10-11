<script lang="ts">
  // ChatGPT sign-in: the browser opens, the answer comes when the sign-in is done.
  import { call } from "../lib/core";
  import { launcher } from "../lib/launcher.svelte";
  import Icon from "./Icon.svelte";

  let busy = $state(false);
  let failed = $state<string | null>(null);
  const signedIn = $derived(launcher.state?.signed_in ?? false);

  async function signIn() {
    busy = true;
    failed = null;
    try {
      const result = await call<{ ok: boolean; signed_in: boolean }>("login.chatgpt");
      if (!result.ok) failed = "Вход не завершён";
      await launcher.refresh();
      launcher.recheck();
    } catch (err) {
      failed = String(err);
    } finally {
      busy = false;
    }
  }
</script>

<div class="signin">
  {#if signedIn}
    <span class="chip"><Icon name="check" size={14} />вход в ChatGPT выполнен</span>
    <button class="link" onclick={signIn} disabled={busy}>{busy ? "Жду браузер…" : "Войти другим аккаунтом"}</button>
  {:else}
    <button class="btn btn-primary" onclick={signIn} disabled={busy}>{busy ? "Жду вход в браузере…" : "Войти через ChatGPT"}</button>
    <span class="faint">Откроется браузер. Реплики NPC будут тратить лимит подписки.</span>
  {/if}
  {#if failed}<span class="failed" role="alert">{failed}</span>{/if}
</div>

<style>
  .signin {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px 18px;
    padding: 12px 0;
  }
  .link {
    font-size: 15px;
  }
  .failed {
    color: var(--bad-text);
    font-size: 14px;
  }
</style>
