<script setup lang="ts">
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { McpScope } from "../api";
import AppSwitch from "./AppSwitch.vue";

/** Read is the mandatory base; turning it off asks to revoke the connection instead. */
const props = defineProps<{ modelValue: McpScope[]; disabled?: boolean; readLocked?: boolean }>();
const emit = defineEmits<{ "update:modelValue": [scopes: McpScope[]]; revoke: [] }>();
const { t } = useI18n();

const READ: McpScope = "cartalytix:read";
const SCOPES: Array<{ value: McpScope; title: string; hint: string }> = [
  { value: READ, title: "mcpScopeReadTitle", hint: "mcpScopeRead" },
  { value: "cartalytix:categories:write", title: "mcpScopeCategoriesWriteTitle", hint: "mcpScopeCategoriesWrite" },
  { value: "cartalytix:calculator", title: "mcpScopeCalculatorTitle", hint: "mcpScopeCalculator" },
  { value: "cartalytix:exclusions:write", title: "mcpScopeExclusionsWriteTitle", hint: "mcpScopeExclusionsWrite" },
  { value: "cartalytix:receipts:write", title: "mcpScopeReceiptsWriteTitle", hint: "mcpScopeReceiptsWrite" },
];
const OPTIONAL = SCOPES.map((scope) => scope.value).filter((value) => value !== READ);

const allEnabled = computed(() => OPTIONAL.every((value) => props.modelValue.includes(value)));

function emitScopes(optional: McpScope[]): void {
  emit("update:modelValue", [READ, ...OPTIONAL.filter((value) => optional.includes(value))]);
}

function toggle(scope: McpScope, enabled: boolean): void {
  if (scope === READ) {
    if (!enabled) emit("revoke");
    return;
  }
  emitScopes(enabled ? [...props.modelValue, scope] : props.modelValue.filter((value) => value !== scope));
}
</script>

<template>
  <div class="scope-switches">
    <div class="scope-row scope-all">
      <div>
        <strong>{{ t("mcpAllowAll") }}</strong>
        <p>{{ t("mcpAllowAllHint") }}</p>
      </div>
      <AppSwitch :model-value="allEnabled" :disabled="disabled" :label="t('mcpAllowAll')" @update:model-value="emitScopes($event ? OPTIONAL : [])" />
    </div>
    <div v-for="scope in SCOPES" :key="scope.value" class="scope-row">
      <div>
        <strong>{{ t(scope.title) }}</strong>
        <p>{{ t(scope.hint) }}</p>
      </div>
      <AppSwitch
        :model-value="scope.value === READ || modelValue.includes(scope.value)"
        :disabled="disabled || (scope.value === READ && readLocked)"
        :label="t(scope.title)"
        @update:model-value="toggle(scope.value, $event)"
      />
    </div>
  </div>
</template>

<style scoped>
.scope-switches { display: grid; gap: 2px; }
.scope-row { display: flex; align-items: center; justify-content: space-between; gap: 20px; padding: 12px 0; }
.scope-row + .scope-row { border-top: 1px solid var(--border); }
.scope-row > div { min-width: 0; }
.scope-row strong { font-size: 13px; font-weight: 550; }
.scope-row p { margin-top: 4px; color: var(--text-muted); font-size: 12px; line-height: 1.7; }
.scope-all strong { font-weight: 650; }
</style>
