<script lang="ts">
  // Which screen the window shows: the wizard until the setup is complete, the game while it runs,
  // the failure of the last start until dismissed, otherwise the main screen or the settings.
  import { onMount } from "svelte";
  import { launcher } from "./lib/launcher.svelte";
  import Failed from "./screens/Failed.svelte";
  import Main from "./screens/Main.svelte";
  import Running from "./screens/Running.svelte";
  import Settings from "./screens/Settings.svelte";
  import Wizard from "./screens/Wizard.svelte";

  let view = $state<"main" | "settings" | "wizard">("main");
  let dismissed = $state<number | null>(null); // the start time of the failed session put aside
  let loadError = $state<string | null>(null);

  onMount(() => {
    launcher
      .init()
      .then(() => {
        // the first setup stays open until its "Готово", even once the folder alone makes it complete
        if (!launcher.state?.configured) view = "wizard";
      })
      .catch((err) => (loadError = String(err)));
  });

  const screen = $derived.by(() => {
    const s = launcher.state;
    if (launcher.coreDown) return "core";
    if (!s) return loadError ? "core" : "loading";
    if (s.session?.state === "running") return "running";
    if (s.session?.failed && s.session.code !== 0 && dismissed !== s.session.started) return "failed";
    if (view === "wizard" || !s.configured) return "wizard";
    return view;
  });
</script>

{#if screen === "loading"}
  <div class="center" data-tauri-drag-region><span class="display">Gothic II</span></div>
{:else if screen === "core"}
  <div class="center" data-tauri-drag-region>
    <h1 class="display">Ядро лаунчера остановилось</h1>
    <p class="muted selectable">{loadError ?? "Подробности в ~/Library/Logs/GothicAI/launcher.log"}</p>
  </div>
{:else if screen === "running"}
  <Running />
{:else if screen === "failed"}
  <Failed onSettings={() => { dismissed = launcher.state!.session!.started; view = "settings"; }} onDismiss={() => (dismissed = launcher.state!.session!.started)} />
{:else if screen === "wizard"}
  <Wizard onDone={() => (view = "main")} />
{:else if screen === "settings"}
  <Settings onBack={() => (view = "main")} onWizard={() => (view = "wizard")} />
{:else}
  <Main onSettings={() => (view = "settings")} />
{/if}

<style>
  .center {
    height: 100%;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    padding: 32px;
    text-align: center;
    font-size: 28px;
  }
  .center p {
    font-size: 15px;
  }
</style>
