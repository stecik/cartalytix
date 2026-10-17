<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, ref, useId, watch } from "vue";
import AppIcon from "./AppIcon.vue";

const props = defineProps<{ modelValue: string; options: ReadonlyArray<{ value: string; label: string; icon?: string }>; label: string; compact?: boolean }>();
const emit = defineEmits<{ "update:modelValue": [value: string] }>();
const id = useId();
const root = ref<HTMLElement | null>(null);
const trigger = ref<HTMLButtonElement | null>(null);
const listbox = ref<HTMLElement | null>(null);
const open = ref(false);
const active = ref(0);
const selected = computed(() => props.options.find((option) => option.value === props.modelValue));

async function show(): Promise<void> {
  active.value = Math.max(0, props.options.findIndex((option) => option.value === props.modelValue));
  open.value = true;
  await nextTick();
  listbox.value?.focus();
}

function close(focusTrigger = true): void {
  open.value = false;
  if (focusTrigger) trigger.value?.focus();
}

function choose(index: number): void {
  const option = props.options[index];
  if (option && option.value !== props.modelValue) emit("update:modelValue", option.value);
  close();
}

function onTriggerKey(event: KeyboardEvent): void {
  if (["ArrowDown", "ArrowUp", "Enter", " "].includes(event.key)) {
    event.preventDefault();
    void show();
  }
}

function onListKey(event: KeyboardEvent): void {
  const last = props.options.length - 1;
  const moves: Record<string, number> = { ArrowDown: Math.min(last, active.value + 1), ArrowUp: Math.max(0, active.value - 1), Home: 0, End: last };
  if (event.key in moves) {
    event.preventDefault();
    active.value = moves[event.key] ?? active.value;
  } else if (event.key === "Enter" || event.key === " ") {
    event.preventDefault();
    choose(active.value);
  } else if (event.key === "Escape") {
    event.preventDefault();
    close();
  } else if (event.key === "Tab") {
    close(false);
  }
}

function onOutside(event: Event): void {
  if (!(event.target instanceof Node && root.value?.contains(event.target))) close(false);
}

watch(open, (value) => {
  if (value) document.addEventListener("pointerdown", onOutside);
  else document.removeEventListener("pointerdown", onOutside);
});
onBeforeUnmount(() => document.removeEventListener("pointerdown", onOutside));
</script>

<template>
  <div ref="root" :class="['app-select', { compact }]">
    <button
      ref="trigger"
      type="button"
      class="app-select-trigger"
      aria-haspopup="listbox"
      :aria-expanded="open"
      :aria-controls="`${id}-list`"
      :aria-label="label"
      @click="open ? close() : show()"
      @keydown="onTriggerKey"
    >
      <span class="app-select-value"><img v-if="selected?.icon" :src="selected.icon" class="app-select-icon" alt="" />{{ selected?.label ?? modelValue }}</span><AppIcon name="chevron" />
    </button>
    <ul
      v-if="open"
      :id="`${id}-list`"
      ref="listbox"
      role="listbox"
      class="app-select-list"
      tabindex="-1"
      :aria-label="label"
      :aria-activedescendant="`${id}-${active}`"
      @keydown="onListKey"
    >
      <li
        v-for="(option, index) in options"
        :id="`${id}-${index}`"
        :key="option.value"
        role="option"
        :aria-selected="option.value === modelValue"
        :class="{ active: index === active }"
        @click="choose(index)"
        @pointermove="active = index"
      >
        <span class="app-select-value"><img v-if="option.icon" :src="option.icon" class="app-select-icon" alt="" />{{ option.label }}</span>
      </li>
    </ul>
  </div>
</template>

<style scoped>
.app-select { position: relative; display: inline-block; }
.app-select-trigger { display: inline-flex; align-items: center; justify-content: space-between; gap: 10px; width: 100%; min-height: 40px; padding: 0 12px 0 14px; border: 1px solid #cbd1c3; border-radius: 6px; background: #fbfbf6; color: var(--green); font-size: 13px; transition: border-color 0.16s, box-shadow 0.16s, background 0.16s; }
.app-select-trigger:hover { border-color: #a9b3a2; }
.app-select-trigger:focus-visible,
.app-select-trigger[aria-expanded="true"] { outline: 0; border-color: #67856c; box-shadow: 0 0 0 2px #dfe6d9; }
.app-select-trigger svg { width: 14px; height: 14px; color: var(--muted); transition: transform 0.16s; }
.app-select-trigger[aria-expanded="true"] svg { transform: rotate(180deg); }
.compact .app-select-trigger { min-height: 34px; gap: 6px; padding: 0 10px 0 14px; border-color: #dfe2d8; border-radius: 999px; font-size: 12px; }
.app-select-list { position: absolute; z-index: 30; top: calc(100% + 6px); left: 0; min-width: max(100%, 150px); margin: 0; padding: 4px; list-style: none; background: #fbfbf6; border: 1px solid #e2e5dc; border-radius: 10px; box-shadow: 0 12px 32px #0f2a2029; outline: 0; }
.compact .app-select-list { left: auto; right: 0; }
.app-select-list li { display: flex; align-items: center; justify-content: space-between; gap: 18px; padding: 8px 10px; border-radius: 6px; font-size: 13px; color: var(--green); cursor: pointer; white-space: nowrap; }
.app-select-list li::after { content: ""; width: 14px; flex-shrink: 0; text-align: center; font-size: 12px; }
.app-select-list li.active { background: #eef0e7; }
.app-select-value { display: inline-flex; align-items: center; gap: 8px; min-width: 0; }
.app-select-icon { width: 18px; height: 18px; object-fit: contain; flex-shrink: 0; }
.app-select-list li[aria-selected="true"] { font-weight: 600; }
.app-select-list li[aria-selected="true"]::after { content: "✓"; }
</style>
