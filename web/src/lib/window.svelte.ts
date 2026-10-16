import { api } from "./api";
import { dowse } from "./app.svelte";
import { search } from "./search.svelte";

class WindowMode {
  switching = $state(false);

  async change(compact: boolean): Promise<void> {
    if (this.switching) return;
    this.switching = true;
    try {
      const route = search.route(compact);
      await api.window(compact ? "popup" : "main", route);
      dowse.go(route);   // also supports the development server in a browser
    } catch (error) {
      dowse.error = String((error as Error).message ?? error);
    } finally {
      this.switching = false;
    }
  }
}

export const windowMode = new WindowMode();
