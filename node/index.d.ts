import { Writable } from 'stream';

export const SCHEMA_VERSION: string;
export const LIB_VERSION: string;

export interface IncidentBufferOptions {
  service: string;
  serviceVersion?: string;
  dataDir: string;
  capacity?: number;
  maxFieldBytes?: number;
  onCapture?: (envelope: any, path: string) => void;
  exporter?: HttpExporter;
}

export interface CaptureOptions {
  trigger: string;
  error?: Error;
  requestId?: string;
  jobId?: string;
  traceId?: string;
  metadata?: Record<string, unknown>;
}

export class IncidentBuffer {
  constructor(opts: IncidentBufferOptions);
  debug(msg: string, fields?: Record<string, unknown>): void;
  info(msg: string, fields?: Record<string, unknown>): void;
  warn(msg: string, fields?: Record<string, unknown>): void;
  error(msg: string, fields?: Record<string, unknown>): void;
  stream(): Writable;
  capture(opts: CaptureOptions): string;
}

export interface HttpExporterOptions {
  url: string;
  authToken: string;
  maxQueue?: number;
  maxRetries?: number;
  timeoutMs?: number;
}

export class HttpExporter {
  constructor(opts: HttpExporterOptions);
  enqueue(path: string, envelope: any): void;
}
