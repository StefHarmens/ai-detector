declare const __AI_DETECTOR_WEB_VERSION__: string;

// The release this page was built from, e.g. "v0.9.0"; "dev" when run from source.
export const version = __AI_DETECTOR_WEB_VERSION__;

// What /versie answers.
export interface Versions {
	// The web server that answers now; after an update it is newer than the
	// page that is open.
	web: string;
	// Null when the detector does not answer; version null for detectors
	// before v0.9.1, which do not say it.
	detector: { version: string | null } | null;
}
