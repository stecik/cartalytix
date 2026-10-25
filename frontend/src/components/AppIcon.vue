<script setup lang="ts">
defineProps<{
  name:
    | "overview"
    | "receipts"
    | "items"
    | "categories"
    | "calculator"
    | "connections"
    | "settings"
    | "gear"
    | "arrow"
    | "logout"
    | "download"
    | "trash"
    | "chevron"
    | "plus"
    | "accounts"
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
    <template v-else-if="name === 'items'">
      <path d="M4 9h16l-1.5 11h-13L4 9Z" />
      <path d="M8 9l3-5M16 9l-3-5M9 13v4M15 13v4" />
    </template>
    <template v-else-if="name === 'categories'">
      <path d="M3 3h7l11 11-7 7L3 10V3Z" /><circle cx="7" cy="7" r="1" />
    </template>
    <template v-else-if="name === 'calculator'">
      <rect x="5" y="3" width="14" height="18" rx="2" />
      <path d="M8 7h8M8.5 12h.01M12 12h.01M15.5 12h.01M8.5 16h.01M12 16h.01M15.5 16h.01" />
    </template>
    <template v-else-if="name === 'connections'">
      <path d="M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7" />
      <path d="M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7" />
    </template>
    <template v-else-if="name === 'gear'">
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33h.08A1.65 1.65 0 0 0 10 3.17V3a2 2 0 0 1 4 0v.09A1.65 1.65 0 0 0 15 4.6a1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82v.08A1.65 1.65 0 0 0 20.83 10H21a2 2 0 0 1 0 4h-.09A1.65 1.65 0 0 0 19.4 15Z" />
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
    <template v-else-if="name === 'accounts'">
      <circle cx="9" cy="8" r="3.5" /><path d="M2.5 20a6.5 6.5 0 0 1 13 0M16 4.5a3.5 3.5 0 0 1 0 7M18 14a6.5 6.5 0 0 1 3.5 6" />
    </template>
    <path v-else d="M12 3v12m-4-4 4 4 4-4M4 16v5h16v-5" />
  </svg>
</template>
