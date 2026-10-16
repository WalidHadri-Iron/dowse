import type { ModelJob } from "./api";

/** Completion is explicit; byte counts (often zero for a device switch) never mean busy or ready. */
export function preparationBusy(job: Pick<ModelJob, "state"> | null, requesting = false): boolean {
  return requesting || job?.state === "running";
}

export function preparationLabel(job: Pick<ModelJob, "kind" | "stage">): string {
  const labels: Record<string, string> = job.kind === "processor" ? {
    runtime: "Preparing the reading engine", model: "Preparing the reading device", validate: "Checking the reading device",
    speed: "Measuring this PC's speed", activate: "Switching the reading device", done: "Reading device ready",
    failed: "Reading device couldn't be changed", cancelled: "Reading device change cancelled",
  } : {
    runtime: "Preparing the search engine", model: "Downloading the search model", validate: "Checking the model",
    speed: "Measuring this PC's speed", activate: "Finishing the current file and activating the model",
    done: "Model ready", failed: "Preparation couldn't finish", cancelled: "Preparation cancelled",
  };
  return labels[job.stage] ?? (job.kind === "processor" ? "Preparing the reading device" : "Preparing the model");
}
