export const STAGES = ['approved', 'rejected', 'unvalidated'] as const;
export type Stage = (typeof STAGES)[number];

export const DEFAULT_SCHEMA_URL = 'https://raw.githubusercontent.com/StefHarmens/ai-detector/main/config/config.schema.json';

export interface DetectorConfig {
    detection: {
        source: string[];
        [key: string]: unknown;
    };
    yolo?: {
        model: string;
        confidence: number;
        frames_min: number;
    };
    exporters?: {
        telegram?: TelegramConfig[];
        [key: string]: unknown[] | undefined;
    };
    [key: string]: unknown;
}

export interface TelegramConfig {
    token: string;
    chat: string;
    alert_every?: number;
}

export interface Config {
    $schema?: string;
    detectors: DetectorConfig[];
    [key: string]: unknown;
}

export interface AppConfig {
    streams: StreamMeta[];
    telegrams: TelegramMeta[];
    detectors: DetectorMeta[];
}

export interface DetectorMeta {
    label: string;
}

export interface TelegramMeta extends TelegramConfig {
    label: string;
}

export interface StreamMeta {
    label?: string;
    source: string;
}

// metadata.json of a detection saved by the disk exporter.
export interface Metadata {
    type: string;
    timestamp: string;
    validated: boolean | null;
    confidence: number;
    confidences: Record<string, number>;
    detections: number;
    start: string;
    end: string;
    duration: number;
    crop?: { x1: number; y1: number; x2: number; y2: number } | null;
    camera?: string | null;
    // Whether hires-best.jpg (whole 4K frame) and hires.jpg (4K crop) exist.
    hires?: boolean;
    width?: number | null;
    height?: number | null;
}
