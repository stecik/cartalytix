<script lang="ts">
import type { InjectionKey, Ref } from "vue";

/** Selector of the open dialog once mounted; popups teleport there because a modal sits in the top layer above <body>. */
export const dialogTarget: InjectionKey<Readonly<Ref<string | null>>> = Symbol("dialogTarget");
</script>

<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, provide, ref, useId } from "vue";
import { useI18n } from "vue-i18n";
import IconButton from "./IconButton.vue";

const props = withDefaults(defineProps<{ title: string; busy?: boolean }>(), { busy: false });
const emit = defineEmits<{ close: [] }>();
const { t } = useI18n();
const dialog = ref<HTMLDialogElement | null>(null);
const titleId = useId();
const dialogId = useId();
const target = ref<string | null>(null);
provide(dialogTarget, target);
let previousFocus: HTMLElement | null = null;

onMounted(async () => {
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  if (dialog.value?.showModal) dialog.value.showModal();
  else dialog.value?.setAttribute("open", "");
  // Children mount first, so the selector only becomes valid now.
  target.value = `#${dialogId}`;
  await nextTick();
  dialog.value?.querySelector<HTMLElement>("[autofocus], input:not([type=checkbox]), [data-action=cancel], button")?.focus();
});
onBeforeUnmount(() => {
  dialog.value?.close?.();
  if (previousFocus?.isConnected) previousFocus.focus();
});

function keyboard(event: KeyboardEvent): void {
  if (event.key === "Escape") {
    event.preventDefault();
    if (!props.busy) emit("close");
  }
  if (event.key !== "Tab") return;
  const items = [...(dialog.value?.querySelectorAll<HTMLElement>("button:not(:disabled), input:not(:disabled), select:not(:disabled), [tabindex='0']") ?? [])];
  const first = items[0];
  const last = items.at(-1);
  if (!first) { event.preventDefault(); dialog.value?.focus(); return; }
  if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
  else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
}
</script>

<template>
  <dialog :id="dialogId" ref="dialog" class="app-dialog" :aria-labelledby="titleId" :aria-busy="busy" tabindex="-1" @keydown="keyboard" @cancel.prevent="!busy && emit('close')" @click="($event.target === dialog) && !busy && emit('close')">
    <div class="dialog-content">
      <header><h2 :id="titleId">{{ title }}</h2><IconButton class="dialog-close" :label="t('dismiss')" :disabled="busy" @click="emit('close')">×</IconButton></header>
      <slot />
    </div>
  </dialog>
</template>

<style>
.app-dialog { width: min(460px, calc(100% - 32px)); max-height: calc(100dvh - 48px); overflow: auto; color: var(--text); background: var(--bg-surface); border: 1px solid var(--border-overlay); border-radius: 10px; padding: 0; box-shadow: 0 18px 80px var(--shadow-overlay); }
.app-dialog::backdrop { background: var(--backdrop); }
.dialog-content { padding: 26px; }
.dialog-content > header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.dialog-content h2 { font-size: 20px; font-weight: 600; letter-spacing: -.5px; }
.dialog-content > p { color: var(--text-muted); font-size: 13px; line-height: 1.7; margin: 12px 0; overflow-wrap: anywhere; }
.dialog-content form { margin-top: 18px; }
.dialog-content .dialog-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; margin-top: 24px; }
.dialog-content .pause-checkbox { display: flex; align-items: flex-start; gap: 10px; font-size: 13px; line-height: 1.6; margin-top: 20px; }
.dialog-content input[type=checkbox] { width: 17px; height: 17px; accent-color: var(--accent); flex-shrink: 0; margin: 2px 0 0; }
@media (max-width: 390px) { .dialog-content { padding: 20px; } }
</style>
