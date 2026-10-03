// The detector serves the cow data on this computer only; the web interface
// shows it on the network. See "api" in config.json.
export function detectorApiUrl(): string {
	return (process.env.DETECTOR_API_URL || 'http://127.0.0.1:8765').replace(/\/+$/, '');
}
