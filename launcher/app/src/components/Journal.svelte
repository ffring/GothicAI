<script lang="ts">
  import { clock, type Line } from "../lib/launcher.svelte";

  let { lines, limit }: { lines: Line[]; limit?: number } = $props();
  const shown = $derived(limit ? lines.slice(-limit) : lines);
  let box: HTMLOListElement | undefined = $state();

  $effect(() => {
    void shown.length; // keep the newest line in view
    if (box) box.scrollTop = box.scrollHeight;
  });
</script>

<ol bind:this={box} class="journal selectable" aria-live="polite">
  {#each shown as line, i (i + ":" + line.t)}
    <li class={line.tone}>
      <span class="time">{clock(line.t)}</span>
      <span class="what">
        {line.text}
        {#if line.meta}<span class="meta">{line.meta}</span>{/if}
      </span>
    </li>
  {:else}
    <li class="empty">Здесь появятся реплики и события игры</li>
  {/each}
</ol>

<style>
  .journal {
    flex: 1;
    min-height: 0;
    overflow-y: auto;
    margin: 0;
    padding: 12px 14px;
    list-style: none;
    display: flex;
    flex-direction: column;
    gap: 7px;
    background: var(--sunk);
    border: 1px solid var(--line);
    border-radius: 6px;
    font-size: 14px;
    color: var(--log);
  }
  li {
    display: flex;
    gap: 12px;
  }
  .time {
    flex: none;
    font-family: var(--mono);
    font-size: 12px;
    line-height: 20px;
    color: #8d7f6d;
  }
  .meta {
    margin-left: 8px;
    font-size: 12px;
    color: var(--text-3);
  }
  .warn {
    color: var(--warn);
  }
  .bad {
    color: var(--bad-text);
  }
  .npc .what {
    color: var(--text);
  }
  .empty {
    color: var(--text-3);
  }
</style>
