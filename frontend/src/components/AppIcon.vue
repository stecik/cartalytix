<script setup lang="ts">
defineProps<{
  name:
    | "overview"
    | "receipts"
    | "connections"
    | "settings"
    | "arrow"
    | "logout"
    | "download"
    | "trash"
    | "chevron"
    | "plus"
    | "mark";
}>();
</script>

<template>
  <svg
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    stroke-width="1.65"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
  >
    <template v-if="name === 'overview'">
      <path d="M3 3h7v7H3zM14 3h7v7h-7zM3 14h7v7H3zM14 14h7v7h-7z" />
    </template>
    <template v-else-if="name === 'receipts' || name === 'mark'">
      <path d="M6 3h12v18l-3-2-3 2-3-2-3 2V3Z" />
      <path d="M9 8h6M9 12h4" />
    </template>
    <template v-else-if="name === 'connections'">
      <path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" />
      <path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" />
    </template>
    <template v-else-if="name === 'settings'">
      <path d="M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12" />
      <circle cx="16" cy="6" r="2" /><circle cx="10" cy="12" r="2" /><circle cx="18" cy="18" r="2" />
    </template>
    <path v-else-if="name === 'arrow'" d="M4 12h15m-5-5 5 5-5 5" />
    <path v-else-if="name === 'logout'" d="M10 4H4v16h6m0-8h11m-4-4 4 4-4 4" />
    <template v-else-if="name === 'trash'"><path d="M4 7h16M9 7V4h6v3M6 7l1 13h10l1-13M10 11v5M14 11v5" /></template>
    <path v-else-if="name === 'chevron'" d="m8 10 4 4 4-4" />
    <path v-else-if="name === 'plus'" d="M12 5v14M5 12h14" />
    <path v-else d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5" />
  </svg>
</template>
