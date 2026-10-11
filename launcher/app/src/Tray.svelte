<script lang="ts">
  // The menu bar panel: the game's state and the newest journal lines while the window is hidden.
  import { onMount } from "svelte";
  import Icon from "./components/Icon.svelte";
  import Journal from "./components/Journal.svelte";
  import { showWindow } from "./lib/core";
  import { launcher } from "./lib/launcher.svelte";
  import { beforeGame, duringGame } from "./lib/status";

  onMount(() => {
    void launcher.init();
    // the panel stays loaded while hidden: catch up when it is opened again
    const onFocus = () => void launcher.refresh();
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  });

  const s = $derived(launcher.state);
  const running = $derived(s?.session?.state === "running");
  const rows = $derived(
    !s
      ? []
      : running
        ? duringGame(s.session!, launcher.providers, !s.settings.voice.enabled, !s.settings.tts.enabled)
        : beforeGame(launcher.providers, launcher.checking, s.problems, s.settings.game.dir),
  );
</script>

<div class="panel-window">
  <div class="head">
    <span class="name">Gothic II</span>
    <span class="state">{running ? "идёт игра" : "лаунчер"}</span>
  </div>
  <ul aria-label="Состояние">
    {#each rows as row (row.key)}
      <li>
        <span>{row.title}</span>
        <span class="chip-text {row.tone}">
          <Icon name={row.tone === "ok" ? "check" : row.tone === "idle" ? "cross" : "warn"} size={13} />{row.chip}
        </span>
      </li>
    {/each}
  </ul>
  <section aria-labelledby="tray-log">
    <h2 id="tray-log" class="faint">Журнал</h2>
    <Journal lines={launcher.lines} limit={6} />
  </section>
  <div class="actions">
    <button class="btn btn-primary" onclick={() => showWindow()}>Открыть окно</button>
    {#if running}<button class="btn btn-stop" onclick={() => launcher.stop()}>Остановить</button>{/if}
  </div>
</div>

<style>
  .panel-window {
    height: 100%;
    display: flex;
    flex-direction: column;
    gap: 12px;
    padding: 16px;
    background: var(--panel);
    border: 1px solid var(--border);
    font-size: 15px;
  }
  .head {
    display: flex;
    align-items: baseline;
    justify-content: space-between;
  }
  .name {
    font-family: var(--display);
    font-weight: 700;
    font-size: 20px;
    letter-spacing: 0.06em;
  }
  .state {
    font-size: 13px;
    color: var(--ember);
  }
  ul {
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 10px;
    padding: 7px 0;
    border-top: 1px solid var(--line);
  }
  .chip-text {
    display: inline-flex;
    align-items: center;
    gap: 5px;
    font-size: 13px;
    color: var(--ok);
  }
  .chip-text.warn {
    color: var(--warn);
  }
  .chip-text.bad {
    color: var(--bad-text);
  }
  .chip-text.idle {
    color: var(--text-3);
  }
  section {
    flex: 1;
    min-height: 0;
    display: flex;
    flex-direction: column;
    gap: 6px;
  }
  h2 {
    font-weight: 500;
    font-size: 13px;
  }
  .actions {
    display: flex;
    gap: 8px;
  }
  .actions .btn {
    flex: 1;
  }
</style>
