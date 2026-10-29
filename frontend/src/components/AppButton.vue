<script setup lang="ts">
// Native attributes (type, disabled, aria-*) fall through; without a type the button submits its form.
withDefaults(defineProps<{ variant?: "primary" | "secondary" | "danger" | "text"; size?: "md" | "sm" }>(), { variant: "secondary", size: "md" });
</script>

<template>
  <button :class="['button', variant, size]"><slot /></button>
</template>

<style scoped>
.button { display: inline-flex; align-items: center; justify-content: center; gap: 12px; min-height: var(--control-height); padding: 11px 18px; border: 1px solid transparent; border-radius: 5px; font-size: 13px; font-weight: 500; transition: background 0.16s, color 0.16s, transform 0.16s; }
.button.sm { gap: 6px; padding: 8px 14px; font-size: 12px; }
.primary { color: var(--on-accent); background: var(--accent); }
.primary:hover:enabled { background: var(--accent-hover); transform: translateY(-1px); }
.secondary { color: var(--accent); background: transparent; border-color: var(--control); }
.secondary:hover:enabled { background: var(--bg-muted); }
.danger { color: var(--on-danger); background: var(--danger); }
.danger:hover:enabled { background: var(--danger-hover); }
.button.text { gap: 8px; min-height: 40px; padding: 8px 0; border: 0; background: none; color: var(--accent); font-size: 12px; }
.text:hover:enabled { color: var(--text-accent-hover); }
.text :deep(svg) { width: 17px; height: 17px; }
</style>
