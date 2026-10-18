import { mount } from "@vue/test-utils";
import { VueDatePicker } from "@vuepic/vue-datepicker";
import { describe, expect, it } from "vitest";
import AppDateInput from "./components/AppDateInput.vue";
import { createAppI18n } from "./i18n";

function render(props: { modelValue: string; min?: string; max?: string; required?: boolean }) {
  return mount(AppDateInput, { props, global: { plugins: [createAppI18n("cs")] } });
}

describe("date input", () => {
  it("passes ISO bounds and emits ISO dates, empty when cleared", async () => {
    const wrapper = render({ modelValue: "2026-09-09", min: "2026-01-01" });
    const picker = wrapper.getComponent(VueDatePicker);
    expect(picker.props("modelValue")).toBe("2026-09-09");
    expect(picker.props("modelType")).toBe("yyyy-MM-dd");
    expect(picker.props("minDate")).toBe("2026-01-01");
    expect(picker.props("maxDate")).toBeUndefined();
    expect(picker.props("weekStart")).toBe(1);
    picker.vm.$emit("update:model-value", "2026-10-18");
    picker.vm.$emit("update:model-value", null);
    expect(wrapper.emitted("update:modelValue")).toEqual([["2026-10-18"], [""]]);
  });

  it("hides clearing for required dates", () => {
    const picker = render({ modelValue: "2026-09-09", required: true }).getComponent(VueDatePicker);
    expect(picker.props("inputAttrs")).toEqual({ clearable: false, required: true });
  });
});
