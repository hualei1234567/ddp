export interface HealthResponse {
  status: string;
  service: string;
  version: string;
  env: string;
}

export interface PlatformMeta {
  name: string;
  version: string;
  env: string;
  phase: string;
  modules: string[];
}
