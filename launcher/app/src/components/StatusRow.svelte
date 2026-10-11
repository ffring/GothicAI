<script lang="ts">
  import Icon from "./Icon.svelte";

  type Tone = "ok" | "warn" | "bad" | "idle";
  let {
    icon,
    title,
    detail,
    chip,
    tone = "ok",
  }: { icon: "candle" | "mic" | "speaker" | "shield"; title: string; detail: string; chip: string; tone?: Tone } =
    $props();
</script>

<div class="row">
  <Icon name={icon} size={22} color="var(--ember)" />
  <div class="text">
    <div class="title">{title}</div>
    <div class="detail">{detail}</div>
  </div>
  <span class="chip {tone === 'ok' ? '' : tone}">
    {#if chip !== "…"}<Icon name={tone === "ok" ? "check" : tone === "idle" ? "cross" : "warn"} size={14} />{/if}{chip}
  </span>
</div>

<style>
  .row {
    display: flex;
    align-items: flex-start;
    gap: 14px;
    padding: 13px 0;
    border-top: 1px solid var(--line);
  }
  .text {
    flex: 1;
    min-width: 0;
  }
  .title {
    font-weight: 700;
  }
  .detail {
    font-size: 14px;
    color: var(--text-2);
  }
</style>
