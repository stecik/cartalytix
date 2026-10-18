<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref, useId } from "vue";
import { useI18n } from "vue-i18n";

const props = withDefaults(defineProps<{ title: string; busy?: boolean }>(), { busy: false });
const emit = defineEmits<{ close: [] }>();
const { t } = useI18n();
const dialog = ref<HTMLDialogElement | null>(null);
const titleId = useId();
let previousFocus: HTMLElement | null = null;

onMounted(async () => {
  previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  if (dialog.value?.showModal) dialog.value.showModal();
  else dialog.value?.setAttribute("open", "");
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
  <dialog ref="dialog" class="app-dialog" :aria-labelledby="titleId" :aria-busy="busy" tabindex="-1" @keydown="keyboard" @cancel.prevent="!busy && emit('close')" @click="($event.target === dialog) && !busy && emit('close')">
    <div class="dialog-content">
      <header><h2 :id="titleId">{{ title }}</h2><button type="button" class="dialog-close" :aria-label="t('dismiss')" :disabled="busy" @click="emit('close')">×</button></header>
      <slot />
    </div>
  </dialog>
</template>

<style>
.app-dialog { width: min(460px, calc(100% - 32px)); max-height: calc(100dvh - 48px); overflow: auto; color: var(--text); background: var(--white); border: 1px solid var(--border-dialog); border-radius: 10px; padding: 0; box-shadow: 0 18px 80px var(--shadow-dialog); }
.app-dialog::backdrop { background: var(--backdrop-dialog); }
.dialog-content { padding: 26px; }
.dialog-content > header { display: flex; align-items: center; justify-content: space-between; gap: 16px; margin-bottom: 16px; }
.dialog-content h2 { font-size: 20px; font-weight: 600; letter-spacing: -.5px; }
.dialog-close { display: grid; place-items: center; flex-shrink: 0; width: 36px; height: 36px; padding: 0; background: transparent; border: 0; font-size: 24px; color: var(--text-dialog-muted); border-radius: 5px; }
.dialog-close:hover:enabled { background: var(--surface-dialog-close-hover); }
.dialog-content > p { color: var(--text-dialog-muted); font-size: 13px; line-height: 1.7; margin: 12px 0; overflow-wrap: anywhere; }
.dialog-content form { margin-top: 18px; }
.dialog-content .dialog-actions { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 8px; margin-top: 24px; }
.dialog-content .pause-checkbox { display: flex; align-items: flex-start; gap: 10px; font-size: 13px; line-height: 1.6; margin-top: 20px; }
.dialog-content input[type=checkbox] { width: 17px; height: 17px; accent-color: var(--green); flex-shrink: 0; margin: 2px 0 0; }
.button.danger { background: var(--danger); color: var(--white); }
.button.danger:hover:enabled { background: var(--danger-hover); }
@media (max-width: 390px) { .dialog-content { padding: 20px; } }
</style>
