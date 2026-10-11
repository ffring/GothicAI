<script lang="ts">
  // One setting: label and hint from the core's settings models, saved as soon as it changes.
  import { open } from "@tauri-apps/plugin-dialog";
  import { launcher } from "../lib/launcher.svelte";

  type Kind = "toggle" | "text" | "number" | "choice" | "secret" | "folder";
  type Option = { value: string; label: string };
  let {
    key,
    kind = "text",
    options = [],
    placeholder = "",
    title,
    description,
    mono = false,
  }: {
    key: string;
    kind?: Kind;
    options?: Option[];
    placeholder?: string;
    title?: string;
    description?: string;
    mono?: boolean;
  } = $props();

  const info = $derived(launcher.info(key));
  const label = $derived(title ?? info.title ?? key);
  // a select's own labels say enough; the models' hints for it name the raw values (none, voxcpm, …)
  const hint = $derived(description ?? (kind === "choice" ? undefined : info.description));
  const value = $derived(launcher.value(key));
  const id = $derived(`setting-${key.replace(".", "-")}`);
  const choices = $derived(
    options.some((o) => o.value === value) || value == null ? options : [...options, { value: String(value), label: String(value) }],
  );
  let draft = $state("");
  let saving = $state(false);
  let failed = $state<string | null>(null);

  async function save(next: unknown) {
    saving = true;
    failed = null;
    try {
      await launcher.set(key, next === "" ? null : next);
      launcher.recheck();
    } catch (err) {
      failed = String(err);
    } finally {
      saving = false;
    }
  }

  async function pickFolder() {
    const dir = await open({ directory: true, defaultPath: (value as string | null) ?? undefined, title: label });
    if (typeof dir === "string") await save(dir);
  }
</script>

<div class="setting" class:inline={kind === "toggle" || kind === "number"}>
  <div class="text">
    <label for={id}>{label}</label>
    {#if hint}<span class="hint">{hint}</span>{/if}
  </div>
  {#if kind === "toggle"}
    <input {id} type="checkbox" checked={Boolean(value)} disabled={saving} onchange={(e) => save(e.currentTarget.checked)} />
  {:else if kind === "number"}
    <input
      {id}
      type="number"
      step="0.1"
      value={value ?? ""}
      {placeholder}
      onchange={(e) => save(e.currentTarget.value === "" ? null : Number(e.currentTarget.value))}
    />
  {:else if kind === "choice"}
    <select {id} value={value ?? ""} disabled={saving} onchange={(e) => save(e.currentTarget.value)}>
      {#each choices as option (option.value)}
        <option value={option.value}>{option.label}</option>
      {/each}
    </select>
  {:else if kind === "folder"}
    <div class="row">
      <span {id} class="path mono selectable" class:unset={value == null}>{value ?? (placeholder || "не выбрана")}</span>
      <button class="btn btn-ghost" onclick={pickFolder} disabled={saving}>Выбрать…</button>
    </div>
  {:else if kind === "secret"}
    {#if value}
      <div class="row">
        <span class="chip">ключ сохранён</span>
        <button class="link" onclick={() => save(null)} disabled={saving}>Удалить ключ</button>
      </div>
    {:else}
      <form class="row" onsubmit={(e) => { e.preventDefault(); if (draft) void save(draft).then(() => (draft = "")); }}>
        <input {id} type="password" bind:value={draft} placeholder="ключ API" autocomplete="off" />
        <button class="btn btn-ghost" type="submit" disabled={!draft || saving}>Сохранить</button>
      </form>
    {/if}
  {:else}
    <input
      {id}
      type="text"
      class:mono
      value={value ?? ""}
      {placeholder}
      spellcheck="false"
      autocomplete="off"
      onchange={(e) => save(e.currentTarget.value.trim())}
    />
  {/if}
  {#if failed}<span class="failed" role="alert">{failed}</span>{/if}
</div>

<style>
  .setting {
    display: flex;
    flex-direction: column;
    gap: 6px;
    padding: 12px 0;
    border-bottom: 1px solid var(--line);
  }
  .setting.inline {
    flex-direction: row;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
  }
  .text {
    display: flex;
    flex-direction: column;
  }
  label {
    font-weight: 700;
  }
  .hint {
    font-size: 14px;
    color: var(--text-2);
  }
  .row {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    gap: 10px;
  }
  .row input {
    flex: 1 1 260px;
  }
  .path {
    flex: 1 1 260px;
    min-height: 40px;
    display: flex;
    align-items: center;
    padding: 0 12px;
    background: var(--raised);
    border: 1px solid var(--control);
    border-radius: 4px;
    overflow-x: auto;
    white-space: nowrap;
  }
  .path.unset {
    color: var(--text-3);
  }
  input[type="number"] {
    width: 100px;
  }
  input.mono {
    font-family: var(--mono);
    font-size: 13px;
  }
  .failed {
    color: var(--bad-text);
    font-size: 14px;
  }
</style>
