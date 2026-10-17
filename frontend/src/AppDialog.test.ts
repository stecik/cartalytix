import { mount } from "@vue/test-utils";
import { afterEach, describe, expect, it } from "vitest";
import { nextTick } from "vue";
import AppDialog from "./components/AppDialog.vue";
import { createAppI18n } from "./i18n";

afterEach(() => { document.body.innerHTML = ""; });

describe("Confirmation dialog", () => {
  it("labels itself, traps focus, supports Escape and restores focus", async () => {
    const trigger = document.createElement("button");
    document.body.append(trigger);
    trigger.focus();
    const wrapper = mount(AppDialog, {
      props: { title: "Delete data" }, attachTo: document.body,
      slots: { default: '<button data-action="cancel">Cancel</button><button data-action="confirm">Confirm</button>' },
      global: { plugins: [createAppI18n("en")] },
    });
    await nextTick();
    expect(document.getElementById(wrapper.get("dialog").attributes("aria-labelledby")!)?.textContent).toBe("Delete data");
    const last = wrapper.get('[data-action="confirm"]').element as HTMLElement;
    last.focus();
    await wrapper.get("dialog").trigger("keydown", { key: "Tab" });
    expect(document.activeElement).toBe(wrapper.get(".dialog-close").element);
    await wrapper.get("dialog").trigger("keydown", { key: "Escape" });
    expect(wrapper.emitted("close")).toHaveLength(1);
    wrapper.unmount();
    expect(document.activeElement).toBe(trigger);
  });

  it("cannot dismiss while an irreversible operation is being saved", async () => {
    const wrapper = mount(AppDialog, { props: { title: "Delete data", busy: true }, global: { plugins: [createAppI18n("en")] } });
    await wrapper.get("dialog").trigger("keydown", { key: "Escape" });
    await wrapper.get("dialog").trigger("cancel");
    await wrapper.get("dialog").trigger("click");
    expect(wrapper.emitted("close")).toBeUndefined();
    expect(wrapper.get(".dialog-close").attributes("disabled")).toBeDefined();
    wrapper.unmount();
  });
});
