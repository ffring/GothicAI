<script lang="ts">
  // The window's top: it doubles as the title bar (the traffic lights sit over its left corner).
  import type { Snippet } from "svelte";

  let { subtitle, tone = "ember", children }: { subtitle: string; tone?: "ember" | "bad"; children?: Snippet } =
    $props();
</script>

<header data-tauri-drag-region>
  <div class="brand" data-tauri-drag-region>
    <span class="name">Gothic II</span>
    <span class="sub">{subtitle}</span>
  </div>
  {@render children?.()}
</header>
<div class="ornament" aria-hidden="true">
  <div></div>
  <i class:bad={tone === "bad"}></i>
  <div></div>
</div>

<style>
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    flex-wrap: wrap;
    gap: 12px;
    padding: 34px 32px 12px;
  }
  .brand {
    display: flex;
    align-items: baseline;
    gap: 12px;
  }
  .name {
    font-family: var(--display);
    font-weight: 700;
    font-size: 26px;
    letter-spacing: 0.08em;
  }
  .sub {
    font-family: var(--display);
    font-weight: 500;
    font-size: 18px;
    letter-spacing: 0.06em;
    color: var(--ember);
  }
  .ornament {
    display: flex;
    align-items: center;
    gap: 10px;
    padding: 0 32px;
  }
  .ornament div {
    flex: 1;
    height: 1px;
    background: var(--border);
  }
  .ornament i {
    width: 7px;
    height: 7px;
    border: 1px solid var(--ember);
    transform: rotate(45deg);
  }
  .ornament i.bad {
    border-color: var(--bad);
  }
</style>
