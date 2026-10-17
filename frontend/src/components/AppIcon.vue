<script setup lang="ts">
defineProps<{
  name:
    | "overview"
    | "receipts"
    | "connections"
    | "arrow"
    | "logout"
    | "download"
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
      <path
        d="m9 15 6-6M8 17l-1 1a4 4 0 0 1-6-6l4-4a4 4 0 0 1 6 0M16 7l1-1a4 4 0 0 1 6 6l-4 4a4 4 0 0 1-6 0"
        transform="translate(0 -1) scale(.96)"
      />
    </template>
    <path v-else-if="name === 'arrow'" d="M4 12h15m-5-5 5 5-5 5" />
    <path v-else-if="name === 'logout'" d="M10 4H4v16h6m0-8h11m-4-4 4 4-4 4" />
    <path v-else d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5" />
  </svg>
</template>
