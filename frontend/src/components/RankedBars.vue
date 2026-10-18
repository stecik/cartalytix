<script setup lang="ts">
import { computed } from "vue";

export interface RankedRow {
  key: string;
  label: string;
  value: number;
  display: string;
  detail: string;
}

const props = defineProps<{ rows: RankedRow[] }>();
const maximum = computed(() => Math.max(0, ...props.rows.map((row) => row.value)));
</script>

<template>
  <ol class="ranked-bars">
    <li v-for="(row, index) in rows" :key="row.key">
      <span class="rank">{{ index + 1 }}</span>
      <div class="ranked-body">
        <div class="ranked-line">
          <span class="ranked-label" :title="row.label">{{ row.label }}</span>
          <strong class="ranked-value">{{ row.display }}</strong>
        </div>
        <div class="ranked-track" aria-hidden="true">
          <span
            class="ranked-fill"
            :style="{ width: `${maximum ? (row.value / maximum) * 100 : 0}%` }"
          />
        </div>
        <span class="ranked-detail">{{ row.detail }}</span>
      </div>
    </li>
  </ol>
</template>

<style scoped>
.ranked-bars {
  list-style: none;
  margin: 18px 0 0;
  padding: 0;
}
.ranked-bars li {
  display: flex;
  gap: 14px;
  padding: 12px 0;
  border-bottom: 1px solid var(--line);
}
.ranked-bars li:last-child {
  border-bottom: 0;
}
.rank {
  width: 20px;
  font-size: 11px;
  font-variant-numeric: tabular-nums;
  color: var(--text-rank);
  padding-top: 2px;
}
.ranked-body {
  flex: 1;
  min-width: 0;
}
.ranked-line {
  display: flex;
  justify-content: space-between;
  gap: 16px;
  font-size: 13px;
}
.ranked-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.ranked-value {
  font-weight: 600;
  font-variant-numeric: tabular-nums;
  color: var(--green);
  white-space: nowrap;
}
.ranked-track {
  height: 4px;
  margin: 8px 0 6px;
  background: var(--light);
  border-radius: 2px;
  overflow: hidden;
}
.ranked-fill {
  display: block;
  height: 100%;
  background: var(--green);
  border-radius: 2px;
  transition: width 0.5s ease-out;
}
.ranked-bars li:hover .ranked-fill {
  background: var(--green-hover);
}
.ranked-detail {
  font-size: 11px;
  color: var(--muted);
}
</style>
