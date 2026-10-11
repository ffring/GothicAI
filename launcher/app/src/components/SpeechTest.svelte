<script lang="ts">
  // One line in Xardas' voice, through a temporary TTS service, played aloud as it is generated.
  import { call, type SpeechResult } from "../lib/core";
  import { seconds } from "../lib/launcher.svelte";
  import { providerTitle } from "../lib/status";
  import Icon from "./Icon.svelte";

  let busy = $state(false);
  let result = $state<SpeechResult | null>(null);
  let failed = $state<string | null>(null);

  async function run() {
    busy = true;
    failed = null;
    try {
      result = await call<SpeechResult>("test.speech");
    } catch (err) {
      failed = String(err);
    } finally {
      busy = false;
    }
  }
</script>

<section class="test panel" aria-labelledby="speech-test-title">
  <div class="head">
    <button class="play" aria-label="Послушать Ксардаса" onclick={run} disabled={busy}><Icon name="play" size={18} /></button>
    <div>
      <h2 id="speech-test-title">Послушать Ксардаса</h2>
      <span class="faint">{busy ? "Синтезирую и играю…" : "Тем же голосом, что в игре. Inworld тратит символы по своему тарифу"}</span>
    </div>
  </div>
  {#if result}
    <span class="heard">«{result.text}»</span>
    <span class="faint">{providerTitle(result.provider)}, первый звук через {seconds(result.first_chunk_s)}, реплика {seconds(result.audio_s, 1)}</span>
  {/if}
  {#if failed}<span class="failed selectable" role="alert">{failed}</span>{/if}
</section>

<style>
  .test {
    display: flex;
    flex-direction: column;
    gap: 10px;
    padding: 14px 18px;
    margin-top: 12px;
  }
  .head {
    display: flex;
    align-items: center;
    gap: 14px;
  }
  h2 {
    font-size: 16px;
  }
  .play {
    flex: none;
    display: inline-flex;
    align-items: center;
    justify-content: center;
    width: 44px;
    height: 44px;
    padding: 0;
    background: var(--ember);
    border: 0;
    border-radius: 50%;
    color: var(--on-ember);
    cursor: pointer;
  }
  .play:hover:not(:disabled) {
    background: var(--ember-hover);
  }
  .play:disabled {
    opacity: 0.6;
    cursor: default;
  }
  .heard {
    padding: 8px 12px;
    background: var(--sunk);
    border-radius: 4px;
    font-style: italic;
    font-size: 17px;
  }
  .failed {
    color: var(--bad-text);
    font-size: 14px;
  }
</style>
