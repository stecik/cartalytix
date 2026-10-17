import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import AppSelect from "./components/AppSelect.vue";

const options = [{ value: "en", label: "English" }, { value: "cs", label: "Čeština" }];

function render() {
  return mount(AppSelect, { props: { modelValue: "en", options, label: "Language" }, attachTo: document.body });
}

describe("app select", () => {
  it("shows the selected label and picks an option with the mouse", async () => {
    const wrapper = render();
    const trigger = wrapper.get('button[aria-haspopup="listbox"]');
    expect(trigger.text()).toBe("English");
    expect(trigger.attributes("aria-label")).toBe("Language");
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
    await trigger.trigger("click");
    expect(trigger.attributes("aria-expanded")).toBe("true");
    expect(wrapper.get('[role="option"][aria-selected="true"]').text()).toBe("English");
    await wrapper.findAll('[role="option"]').at(1)?.trigger("click");
    expect(wrapper.emitted("update:modelValue")).toEqual([["cs"]]);
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
    wrapper.unmount();
  });

  it("supports keyboard selection and escape", async () => {
    const wrapper = render();
    const trigger = wrapper.get('button[aria-haspopup="listbox"]');
    await trigger.trigger("keydown", { key: "ArrowDown" });
    const listbox = wrapper.get('[role="listbox"]');
    expect(document.activeElement).toBe(listbox.element);
    await listbox.trigger("keydown", { key: "ArrowDown" });
    expect(listbox.attributes("aria-activedescendant")).toBe(wrapper.findAll('[role="option"]').at(1)?.attributes("id"));
    await listbox.trigger("keydown", { key: "Escape" });
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
    expect(document.activeElement).toBe(trigger.element);
    expect(wrapper.emitted("update:modelValue")).toBeUndefined();
    await trigger.trigger("keydown", { key: "ArrowDown" });
    await wrapper.get('[role="listbox"]').trigger("keydown", { key: "ArrowDown" });
    await wrapper.get('[role="listbox"]').trigger("keydown", { key: "Enter" });
    expect(wrapper.emitted("update:modelValue")).toEqual([["cs"]]);
    wrapper.unmount();
  });

  it("closes when clicking outside", async () => {
    const wrapper = render();
    await wrapper.get("button").trigger("click");
    document.body.dispatchEvent(new Event("pointerdown", { bubbles: true }));
    await wrapper.vm.$nextTick();
    expect(wrapper.find('[role="listbox"]').exists()).toBe(false);
    wrapper.unmount();
  });
});
