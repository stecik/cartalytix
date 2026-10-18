import type { InjectionKey, Ref } from "vue";

export const pwaInstallKey: InjectionKey<{
  available: Readonly<Ref<boolean>>;
  install: () => Promise<void>;
}> = Symbol("pwaInstall");
