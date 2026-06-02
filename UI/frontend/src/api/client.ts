import axios from "axios";
import type {
  BurnStartRequest,
  BurnStartAllRequest,
  JobInfo,
  LoadSeries,
  MachineApiRecord,
  Point,
  SlurmAllocation,
  WaveformInfo,
  WsEvent
} from "../types";

const http = axios.create({
  baseURL: "/api",
  timeout: 20000
});

function toPointList(points: Array<[number, number]>): Point[] {
  return points.map(([x, y]) => ({ x, y }));
}

function toWaveform(raw: { name: string; source: "fixtures" | "custom"; points: Array<[number, number]> }): WaveformInfo {
  return {
    name: raw.name,
    source: raw.source,
    points: toPointList(raw.points)
  };
}

export async function fetchMachines(offset = 0, limit = 50): Promise<MachineApiRecord[]> {
  const { data } = await http.get<MachineApiRecord[]>("/machines", {
    params: { offset, limit }
  });
  return data;
}

export async function connectMachine(id: string): Promise<void> {
  await http.post(`/machines/${encodeURIComponent(id)}/connect`);
}

export async function disconnectMachine(id: string): Promise<void> {
  await http.post(`/machines/${encodeURIComponent(id)}/disconnect`);
}

export async function refreshHwInfo(id: string): Promise<void> {
  await http.get(`/machines/${encodeURIComponent(id)}/hwinfo`);
}

export async function fetchWaveforms(): Promise<WaveformInfo[]> {
  const { data } = await http.get<Array<{ name: string; source: "fixtures" | "custom"; points: Array<[number, number]> }>>(
    "/waveforms"
  );
  return data.map(toWaveform);
}

export async function fetchWaveform(name: string): Promise<WaveformInfo> {
  const { data } = await http.get<{ name: string; source: "fixtures" | "custom"; points: Array<[number, number]> }>(
    `/waveforms/${encodeURIComponent(name)}`
  );
  return toWaveform(data);
}

export async function saveWaveform(name: string, points: Point[]): Promise<WaveformInfo> {
  const { data } = await http.post<{ name: string; source: "fixtures" | "custom"; points: Array<[number, number]> }>(
    "/waveforms",
    {
      name,
      points: points.map((point) => [point.x, point.y])
    }
  );
  return toWaveform(data);
}

export async function startBurn(payload: BurnStartRequest): Promise<JobInfo[]> {
  const { data } = await http.post<JobInfo[]>("/burn/start", payload);
  return data;
}

export async function startBurnAll(payload: BurnStartAllRequest): Promise<JobInfo[]> {
  const { data } = await http.post<JobInfo[]>("/burn/start-all", payload);
  return data;
}

export async function stopBurn(machineIds: string[] | "all"): Promise<void> {
  await http.post("/burn/stop", { machine_ids: machineIds });
}

export async function stopJobs(jobIds: string[] | "all"): Promise<void> {
  await http.post("/burn/stop", { job_ids: jobIds });
}

export async function fetchBurnStatus(): Promise<JobInfo[]> {
  const { data } = await http.get<JobInfo[]>("/burn/status");
  return data;
}

export async function fetchAllocation(): Promise<SlurmAllocation> {
  const { data } = await http.get<SlurmAllocation>("/slurm/allocation");
  return data;
}

export async function submitAllocation(nodes: number, timeLimit: string, pollMs: number, sampleMs: number): Promise<SlurmAllocation> {
  const { data } = await http.post<SlurmAllocation>("/slurm/submit", {
    nodes,
    time_limit: timeLimit,
    poll_ms: pollMs,
    sample_ms: sampleMs
  });
  return data;
}

export async function releaseAllocation(): Promise<SlurmAllocation> {
  const { data } = await http.post<SlurmAllocation>("/slurm/release");
  return data;
}

export async function downloadLoadCsv(): Promise<void> {
  let response;
  try {
    response = await http.get<Blob>("/slurm/load.csv", { responseType: "blob" });
  } catch (error) {
    const message = await extractBlobErrorMessage(error);
    if (message) {
      throw new Error(message);
    }
    throw error;
  }
  const href = window.URL.createObjectURL(response.data);
  const link = document.createElement("a");
  link.href = href;
  link.download = filenameFromDisposition(response.headers["content-disposition"]) ?? "burner-load.csv";
  document.body.appendChild(link);
  link.click();
  link.remove();
  window.setTimeout(() => window.URL.revokeObjectURL(href), 0);
}

export async function fetchLoadSeries(maxPoints = 1200, includeNodes = false): Promise<LoadSeries> {
  const { data } = await http.get<LoadSeries>("/slurm/load-series", {
    params: { max_points: maxPoints, include_nodes: includeNodes }
  });
  return data;
}

export async function runUpdate(id: string): Promise<void> {
  await http.post(`/update/${encodeURIComponent(id)}`);
}

export async function applySamplingTime(samplingMs: number, machineIds: string[]): Promise<void> {
  await http.post("/sampling/apply", {
    sampling_ms: samplingMs,
    machine_ids: machineIds
  });
}

export function extractErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const detail = error.response?.data?.detail;
    if (typeof detail === "string") {
      return detail;
    }
    if (error.message) {
      return error.message;
    }
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function filenameFromDisposition(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined;
  }
  const match = /filename="?([^";]+)"?/i.exec(value);
  return match?.[1];
}

async function extractBlobErrorMessage(error: unknown): Promise<string | undefined> {
  if (!axios.isAxiosError(error) || !(error.response?.data instanceof Blob)) {
    return undefined;
  }
  try {
    const text = await error.response.data.text();
    const parsed = JSON.parse(text) as { detail?: unknown };
    return typeof parsed.detail === "string" ? parsed.detail : undefined;
  } catch {
    return undefined;
  }
}

export function openEventSocket(
  onMessage: (event: WsEvent) => void,
  onConnectionChange: (connected: boolean) => void
): () => void {
  let socket: WebSocket | null = null;
  let reconnectTimer: number | null = null;
  let closed = false;

  const connect = () => {
    if (closed) {
      return;
    }
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    socket = new WebSocket(`${protocol}//${window.location.host}/ws`);

    socket.onopen = () => onConnectionChange(true);
    socket.onmessage = (message) => onMessage(JSON.parse(message.data) as WsEvent);
    socket.onclose = () => {
      onConnectionChange(false);
      if (!closed) {
        reconnectTimer = window.setTimeout(connect, 1500);
      }
    };
    socket.onerror = () => {
      socket?.close();
    };
  };

  connect();

  return () => {
    closed = true;
    if (reconnectTimer !== null) {
      window.clearTimeout(reconnectTimer);
    }
    socket?.close();
  };
}
