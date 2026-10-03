import { detectorApiUrl } from '$lib/server/detector-api';
import { version, type Versions } from '$lib/version';
import type { RequestHandler } from './$types';

export const GET: RequestHandler = async () => {
	let detector: Versions['detector'] = null;
	try {
		const response = await fetch(`${detectorApiUrl()}/api/status`, {
			signal: AbortSignal.timeout(3_000)
		});
		if (response.ok) {
			const status = (await response.json()) as { version?: string };
			detector = { version: status.version ?? null };
		}
	} catch {
		// Not running, or still starting.
	}
	return Response.json({ web: version, detector } satisfies Versions, {
		headers: { 'Cache-Control': 'no-store' }
	});
};
