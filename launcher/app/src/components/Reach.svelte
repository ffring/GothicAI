<script lang="ts">
  // Whether the chosen providers of one part answer (from check.providers).
  import type { Part } from "../lib/core";
  import { launcher } from "../lib/launcher.svelte";
  import { lowerFirst } from "../lib/status";
  import Icon from "./Icon.svelte";

  let { part }: { part: Part | undefined } = $props();
</script>

<div class="reach" aria-live="polite">
  {#if launcher.checking && !part}
    <span class="faint">Проверяю…</span>
  {:else if part && part.enabled}
    {#each part.providers as p, i (p.id + i)}
      <span class="item" class:bad={!p.ok}>
        <Icon name={p.ok ? "check" : "cross"} size={14} />
        {i ? "Запасной" : "Основной"}: {p.title} — {lowerFirst(p.detail)}
      </span>
    {/each}
  {/if}
  <button class="link" onclick={() => launcher.checkProviders()} disabled={launcher.checking}>
    {launcher.checking ? "Проверяю…" : "Проверить снова"}
  </button>
</div>

<style>
  .reach {
    display: flex;
    flex-direction: column;
    align-items: flex-start;
    gap: 6px;
    padding: 12px 14px;
    background: var(--sunk);
    border-radius: 4px;
    font-size: 14px;
  }
  .item {
    display: inline-flex;
    align-items: center;
    gap: 8px;
    color: var(--ok);
  }
  .item.bad {
    color: var(--bad-text);
  }
  .link {
    min-height: 28px;
    font-size: 14px;
  }
</style>
